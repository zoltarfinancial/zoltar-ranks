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
