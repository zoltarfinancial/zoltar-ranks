"""Execution-safety contract gates (`data/build/manifest.yaml` → §10).

These are the assertions that decide whether Phase 3's numbers mean anything, so
they are written **before** Phase 3. A gate written after the result it was
supposed to guard tends to get written to pass.

Implemented here: `no_latest_pkl` (rule 4).

**Deliberately NOT here yet: `no_same_bar` and `no_run_ts_execution`.** There is
no execution engine to guard, so any test of them today would pass because the
code does not exist — which produces a green light that means nothing, the exact
failure mode the gates exist to prevent. They stay `not_built` in §10 and
blocker B2 stays open until `analysis/backtest.py` lands. That is the honest
state, and the monitor is supposed to show it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from zoltar_ranks.config import Config

SRC = Path(__file__).resolve().parents[1] / "src" / "zoltar_ranks"

#: The one module allowed to name `*_rankings_*` files. It parses them from
#: `git log` output as FILENAMES only, to build the ground-truth session census
#: (FINDINGS F4). Rule 4 forbids opening them, not knowing they exist.
FILENAME_ONLY_MODULES = {"harvest_sessions.py"}

#: Anything that turns a path into data. A module that names a `_rankings_` file
#: and calls one of these is one edit away from reading in-sample rows.
#:
#: Deliberately NOT `load`/`loads`: `Config.load()` is the config loader and
#: matching on the bare attribute name flags it. `pickle.loads` reaches the
#: archive only via `UpstreamMirror.read_pickle`, which is listed, so the real
#: path is covered without the false positive.
BLOB_READERS = {"read_pickle", "read_pickle_head", "read_blob", "read_parquet",
                "read_csv", "read_json"}


def test_no_latest_pkl_in_config_paths():
    """`*_rankings_latest.pkl` holds `source='train'` rows. Never an input."""
    configured = [*Config().rank_files, *Config().er_files, *Config().shap_files]
    offenders = [p for p in configured if "_rankings_" in p or p.endswith("_rankings_latest.pkl")]
    assert not offenders, (
        f"config declares in-sample file(s) as a harvest input: {offenders}. "
        f"*_rankings_latest.pkl contains train/validate rows scored by the "
        f"CURRENT model; backtesting on it inflates every result silently.")


def test_no_latest_pkl_matches_the_prod_regex():
    """The Rule 4 boundary is `PROD_RE`. Prove it rejects the rankings files."""
    from zoltar_ranks.ingest.harvest_daily_ranks import PROD_RE
    for name in [
        "daily_ranks/all_low_risk_rankings_latest.pkl",
        "daily_ranks/all_low_risk_rankings_20260902_080544.pkl",
        "daily_ranks/low_risk_rankings_20260902_080544-MORNING UPDATE.pkl",
    ]:
        assert PROD_RE.match(name) is None, (
            f"PROD_RE matches {name!r}, so the daily_ranks harvester would open "
            f"an in-sample file. Rule 4 is enforced by this regex.")


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Node ids of every docstring, so documentation is not mistaken for code.

    `harvest_daily_ranks.py` states the Rule 4 boundary in its module docstring.
    Naming the hazard in prose is the opposite of a violation.
    """
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _string_literals(tree: ast.AST) -> list[str]:
    skip = _docstring_ids(tree)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip]


def _called_names(tree: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: p.name)
def test_no_latest_pkl_reaches_a_blob_read(path):
    """A module that NAMES a rankings file must not also be able to READ one.

    Structural, not textual: the rankings filenames legitimately appear in
    `harvest_sessions.py`, which builds the session census from `git log`
    filenames and opens nothing. This asserts that separation holds, so a future
    edit adding a `read_pickle` there fails here rather than silently
    contaminating the archive.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names_rankings = any("_rankings_" in s for s in _string_literals(tree))
    if not names_rankings:
        return
    assert path.name in FILENAME_ONLY_MODULES, (
        f"{path.name} contains a '*_rankings_*' path literal but is not on the "
        f"filename-only allowlist. Rule 4: those files hold in-sample rows.")
    readers = _called_names(tree) & BLOB_READERS
    assert not readers, (
        f"{path.name} names '*_rankings_*' AND calls {sorted(readers)}. It is "
        f"allowed to know those filenames exist, never to open one.")
