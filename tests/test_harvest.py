"""Contract tests. Run these BEFORE trusting any downstream analysis.

`test_upstream_*` hit the network (they clone/fetch the upstream mirror) and are
the ones that catch upstream schema drift. `test_*_pure` are offline.
"""
from __future__ import annotations

import pandas as pd
import pytest

from zoltar_ranks.config import Config
from zoltar_ranks.ingest.harvest_ranks import classify_run, normalize
from zoltar_ranks.sources.git_archive import UpstreamMirror


# --------------------------- offline ---------------------------

def test_classify_run_pure():
    assert classify_run(pd.Timestamp("2026-09-02 00:00:00")) == "placeholder"
    assert classify_run(pd.Timestamp("2026-09-01 07:42:49")) == "morning"
    assert classify_run(pd.Timestamp("2026-09-01 10:30:40")) == "intraday"
    assert classify_run(pd.Timestamp("2026-09-01 14:48:35")) == "intraday"
    assert classify_run(pd.Timestamp("2026-08-27 20:04:56")) == "nightly"


def test_normalize_pure():
    raw = pd.DataFrame({
        "Date": pd.to_datetime(["2026-09-01 07:42:49"] * 2),
        "Symbol": [" aapl ", "MSFT"],
        "Score": [0.01, 0.02],
        "Score_Sharpe": [1.0, 2.0],
        "Score_HoldPeriod": [7.5, 7.5],
        "Close_Price": [200.0, 400.0],
        "Cap_Size": ["Large", "Large"],
        "Sector": ["Tech", "Tech"],
        "Industry": ["HW", "SW"],
        "source": ["validate_oot", "validate_oot"],
    })
    out = normalize(raw, "low", "daily", "deadbeef")
    assert list(out.symbol) == ["AAPL", "MSFT"]
    assert set(out.run_kind) == {"morning"}
    assert out.risk_bucket.unique().tolist() == ["low"]
    assert out.first_seen_sha.unique().tolist() == ["deadbeef"]


def test_normalize_rejects_bad_schema_pure():
    with pytest.raises(ValueError):
        normalize(pd.DataFrame({"Symbol": ["A"]}), "low", "daily", "x")


# --------------------------- network ---------------------------

@pytest.fixture(scope="session")
def mirror():
    cfg = Config.load()
    m = UpstreamMirror(cfg.upstream_url, cfg.mirror_dir, cfg.upstream_branch)
    m.ensure()
    return m


@pytest.mark.network
def test_upstream_schema_unchanged(mirror):
    """Upstream is someone else's repo. If these columns move, everything breaks."""
    df = mirror.read_pickle_head("production/low_risk_PROD_latest.pkl")
    assert {"Date", "Symbol", "Score", "Score_Sharpe", "Score_HoldPeriod",
            "Close_Price", "Cap_Size", "Sector", "Industry", "source"} <= set(df.columns)
    assert df["Date"].dtype.kind == "M"
    assert len(df) > 100_000


@pytest.mark.network
def test_upstream_is_point_in_time(mirror):
    """THE load-bearing assumption: historical scores are never restated.

    If this fails, every backtest built on the archive is contaminated and the
    archive must be rebuilt with restatement tracking.
    """
    path = "production/low_risk_PROD_latest.pkl"
    snaps = mirror.commits_touching(path)
    assert len(snaps) > 5
    new = mirror.read_pickle(snaps[0].sha, path)
    old = mirror.read_pickle(snaps[min(len(snaps) - 1, 60)].sha, path)
    m = old.merge(new, on=["Date", "Symbol"], suffixes=("_o", "_n"))
    assert len(m) > 1000, "no overlap to compare"
    assert (m.Score_o == m.Score_n).mean() == 1.0, "upstream RESTATED historical scores"
    assert (m.Close_Price_o == m.Close_Price_n).mean() == 1.0


@pytest.mark.network
def test_all_feed_is_a_rolling_buffer(mirror):
    """Documents *why* the backfill exists: `all_*` drops old timestamps."""
    path = "production/all_low_risk_PROD_latest.pkl"
    snaps = mirror.commits_touching(path)
    new = mirror.read_pickle(snaps[0].sha, path)
    old = mirror.read_pickle(snaps[min(len(snaps) - 1, 100)].sha, path)
    lost = set(old.Date.unique()) - set(new.Date.unique())
    assert new.Date.nunique() <= 210, "buffer cap changed; the coverage walk's assumptions need review"
    assert lost, "expected the rolling buffer to have dropped old run timestamps"


# --------------------- expected returns / SHAP ---------------------

def test_er_normalize_pure():
    from zoltar_ranks.ingest.harvest_er import normalize as er_normalize
    raw = pd.DataFrame({
        "period": [1, 2],
        "er": [0.001, 0.002],
        "Date": pd.to_datetime(["2026-08-31", "2026-08-31"]),
        "Symbol": [" aapl ", "aapl"],
    })
    out = er_normalize(raw, "daily", "sha1")
    assert list(out.symbol) == ["AAPL", "AAPL"]
    assert list(out.period) == [1, 2]
    assert out.variant.unique().tolist() == ["daily"]


def test_shap_normalize_pure():
    from zoltar_ranks.ingest.harvest_shap import normalize as shap_normalize
    wide = pd.DataFrame(
        {"Slope_3w_woe": [0.1, 0.2], "MA_30_woe": [0.3, 0.4],
         "Feature Category": ["Other", "Momentum"]},
        index=["sony", "dell"],
    )
    values, labels = shap_normalize(wide, "Large", pd.Timestamp("2026-09-01 10:00"), "sha1")
    assert set(values.symbol) == {"SONY", "DELL"}
    assert set(values.feature) == {"Slope_3w_woe", "MA_30_woe"}
    assert len(values) == 4
    assert set(labels.label_name) == {"Feature Category"}


@pytest.mark.network
def test_upstream_er_and_shap_schema(mirror):
    from zoltar_ranks.ingest.harvest_er import normalize as er_normalize
    from zoltar_ranks.ingest.harvest_shap import normalize as shap_normalize
    cfg = Config.load()
    for path, variant in cfg.er_files.items():
        df = er_normalize(mirror.read_pickle_head(path), variant, "head")
        assert sorted(df.period.unique()) == list(range(1, 15)), "ER horizon count changed"
        assert df.as_of_date.nunique() == 1, "ER file should carry exactly one as-of date"
    for path, segment in cfg.shap_files.items():
        values, labels = shap_normalize(
            mirror.read_pickle_head(path), segment, pd.Timestamp("2026-01-01"), "head")
        assert values.feature.nunique() > 100, f"{segment} SHAP feature count collapsed"
        assert values.symbol.nunique() > 10


def test_shap_rejects_positional_index_pure():
    """pandas 3 changed string index dtypes; the guard must catch the real
    failure mode (a positional index) rather than a dtype name."""
    from zoltar_ranks.ingest.harvest_shap import normalize as shap_normalize
    with pytest.raises(ValueError, match="positional index"):
        shap_normalize(pd.DataFrame({"a": [1.0]}), "Large", pd.Timestamp("2026-01-01"), "s")
