"""Central configuration. All paths resolve relative to the repo root unless absolute."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "config.yaml"


@dataclass
class Config:
    # --- upstream source ---
    upstream_url: str = "https://github.com/apod-1/ZoltarFinancial.git"
    upstream_branch: str = "main"
    mirror_dir: Path = REPO_ROOT / "data" / "cache" / "ZoltarFinancial.git"

    # --- local archive ---
    archive_dir: Path = REPO_ROOT / "data" / "archive"
    duckdb_path: Path = REPO_ROOT / "data" / "zoltar.duckdb"
    results_dir: Path = REPO_ROOT / "data" / "results"

    # --- harvest behaviour ---
    # Files inside the upstream repo that carry point-in-time rank snapshots.
    rank_files: dict = field(default_factory=lambda: {
        # path in upstream repo -> (risk_bucket, feed)
        "production/low_risk_PROD_latest.pkl": ("low", "daily"),
        "production/high_risk_PROD_latest.pkl": ("high", "daily"),
        "production/all_low_risk_PROD_latest.pkl": ("low", "all"),
        "production/all_high_risk_PROD_latest.pkl": ("high", "all"),
    })
    er_files: dict = field(default_factory=lambda: {
        "production/er_for_last_date.pkl": "daily",
        "production/er_for_last_date_live.pkl": "live",
    })
    shap_files: dict = field(default_factory=lambda: {
        "production/combined_SHAP_summary_Large_latest.pkl": "Large",
        "production/combined_SHAP_summary_Mid_latest.pkl": "Mid",
        "production/combined_SHAP_summary_Small_latest.pkl": "Small",
    })

    # --- stamping convention cutover (FINDINGS F4) ---
    # Andrew's evening retrain changed how it is STAMPED on 2026-09-02. Before:
    # the next calendar day, usually 00:00:00 or +24h ("forward"). From the
    # cutover: today's date with a real time ("honest"). The session LABEL
    # (AFTERCLOSE UPDATE) is unchanged, so nothing in run_sessions moves.
    #
    # This matters because H11's ~13-hour extended-hours advantage was DERIVED
    # from the forward stamp. Pre- and post-cutover evening rows encode the same
    # physical fact two different ways, so pooling them silently mixes
    # conventions. Never aggregate evening retrains without grouping by
    # `stamp_convention` -- see the ranks_pit view and tests/test_stamp_cutover.py.
    stamp_cutover_date: str = "2026-09-02"

    # --- market data ---
    price_provider: str = "robin_stocks"   # robin_stocks | alpaca | yfinance
    price_cache_dir: Path = REPO_ROOT / "data" / "cache" / "prices"

    # --- baseline strategy (mirrors zoltar.streamlit.app defaults) ---
    baseline_top_x: int = 5
    baseline_omit_first: int = 0
    baseline_risk_bucket: str = "low"
    baseline_gain_threshold: float = 0.02    # +2%
    baseline_loss_threshold: float = -0.01   # -1%
    baseline_ranking_metric: str = "score"

    # --- execution (rule 3) ---
    # The latency between the decision and the fill. NEVER 0: a zero-latency
    # fill uses the price the rank was computed from, which is the single
    # largest source of fake edge (H9, FINDINGS F5).
    #
    # 15 minutes is not arbitrary. A floor of merely "strictly after" is not
    # enough: measured 2026-09-02 on H11, 27 of 138 evening runs pair a
    # forward-stamped retrain with the SAME evening's other build pushed 13-15
    # SECONDS later in the same commit -- a different run_ts, a different file,
    # a return of exactly 0.000%. That passes any positive-latency test while
    # being same-bar in every way that matters, and it biases in the flattering
    # direction. `analysis.execution.latency_floor` rejects <= 0 outright.
    execution_latency_minutes: float = 15.0

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        path = Path(path or DEFAULT_CONFIG)
        cfg = cls()
        if path.exists():
            raw = yaml.safe_load(path.read_text()) or {}
            for k, v in raw.items():
                if not hasattr(cfg, k):
                    raise KeyError(f"Unknown config key: {k}")
                cur = getattr(cfg, k)
                setattr(cfg, k, Path(v) if isinstance(cur, Path) else v)
        for p in (cfg.mirror_dir.parent, cfg.archive_dir, cfg.results_dir,
                  cfg.price_cache_dir, cfg.duckdb_path.parent):
            p.mkdir(parents=True, exist_ok=True)
        return cfg
