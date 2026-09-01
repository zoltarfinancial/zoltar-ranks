-- Zoltar rank archive. DuckDB is the system of record; Parquet is the export.
-- Everything here is POINT-IN-TIME: a row records what the model said at run_ts,
-- and is never updated once written. Corrections arrive as new rows, never edits.

CREATE TABLE IF NOT EXISTS harvest_manifest (
    file_path      VARCHAR NOT NULL,   -- path inside the upstream repo
    commit_sha     VARCHAR NOT NULL,
    committed_at   TIMESTAMP NOT NULL,
    rows_seen      BIGINT,
    rows_inserted  BIGINT,
    harvested_at   TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (file_path, commit_sha)
);

-- One row per (run timestamp, symbol, risk bucket).
CREATE TABLE IF NOT EXISTS ranks (
    run_ts            TIMESTAMP NOT NULL,
    symbol            VARCHAR   NOT NULL,
    risk_bucket       VARCHAR   NOT NULL,   -- 'low' | 'high'
    score             DOUBLE,
    score_sharpe      DOUBLE,
    score_holdperiod  DOUBLE,
    close_price       DOUBLE,
    cap_size          VARCHAR,              -- model segment label: Large | Mid | Small
    sector            VARCHAR,
    industry          VARCHAR,
    src_split         VARCHAR,              -- upstream 'source' col: train/validate/validate_oot
    feed              VARCHAR,              -- 'daily' (append-only archive) | 'all' (rolling buffer)
    run_kind          VARCHAR,              -- 'morning' | 'intraday' | 'nightly' | 'placeholder'
    first_seen_sha    VARCHAR,
    PRIMARY KEY (run_ts, symbol, risk_bucket)
);

CREATE INDEX IF NOT EXISTS ranks_run_ts   ON ranks (run_ts);
CREATE INDEX IF NOT EXISTS ranks_symbol   ON ranks (symbol);

-- Forward expected-return curve, 14 horizons per symbol per as-of date.
CREATE TABLE IF NOT EXISTS expected_returns (
    as_of_date   DATE    NOT NULL,
    symbol       VARCHAR NOT NULL,
    period       INTEGER NOT NULL,     -- 1..14 trading days ahead
    er           DOUBLE,
    variant      VARCHAR NOT NULL,     -- 'daily' | 'live'
    first_seen_sha VARCHAR,
    PRIMARY KEY (as_of_date, symbol, period, variant)
);

-- SHAP feature attributions, long form. Upstream ships one WIDE frame per cap
-- segment, indexed by SYMBOL (the segment's top-scoring names), with ~160-180
-- WOE-binned feature columns plus a 'Feature Category' label column. The file
-- carries no timestamp of its own, so snapshot_ts comes from the git commit.
CREATE TABLE IF NOT EXISTS shap_summary (
    snapshot_ts  TIMESTAMP NOT NULL,
    segment      VARCHAR   NOT NULL,   -- Large | Mid | Small
    symbol       VARCHAR   NOT NULL,
    feature      VARCHAR   NOT NULL,
    value        DOUBLE,
    first_seen_sha VARCHAR,
    PRIMARY KEY (snapshot_ts, segment, symbol, feature)
);

-- The one non-numeric column upstream ships alongside the SHAP values.
CREATE TABLE IF NOT EXISTS shap_labels (
    snapshot_ts  TIMESTAMP NOT NULL,
    segment      VARCHAR   NOT NULL,
    symbol       VARCHAR   NOT NULL,
    label_name   VARCHAR   NOT NULL,   -- e.g. 'Feature Category'
    label_value  VARCHAR,
    PRIMARY KEY (snapshot_ts, segment, symbol, label_name)
);

-- Daily OHLCV, whatever provider we are on. Adjusted flags kept explicit so a
-- provider swap can never silently mix adjusted and raw prices.
CREATE TABLE IF NOT EXISTS prices_daily (
    trade_date DATE    NOT NULL,
    symbol     VARCHAR NOT NULL,
    open       DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
    adjusted   BOOLEAN NOT NULL,
    provider   VARCHAR NOT NULL,
    PRIMARY KEY (trade_date, symbol, provider)
);

-- Intraday bars, used for fill-timing experiments.
CREATE TABLE IF NOT EXISTS prices_intraday (
    ts        TIMESTAMP NOT NULL,   -- bar OPEN time, America/New_York, tz-naive
    symbol    VARCHAR   NOT NULL,
    interval  VARCHAR   NOT NULL,   -- '1min' | '5min' | '1hour'
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
    provider  VARCHAR NOT NULL,
    PRIMARY KEY (ts, symbol, interval, provider)
);

-- Corporate actions, so a 10:1 split never shows up as a -90% "return".
CREATE TABLE IF NOT EXISTS corporate_actions (
    ex_date  DATE    NOT NULL,
    symbol   VARCHAR NOT NULL,
    kind     VARCHAR NOT NULL,   -- 'split' | 'dividend'
    ratio    DOUBLE,             -- split ratio (new/old); NULL for dividends
    amount   DOUBLE,             -- cash amount; NULL for splits
    provider VARCHAR NOT NULL,
    PRIMARY KEY (ex_date, symbol, kind, provider)
);

-- Convenience view: one canonical "morning run" per trading day.
CREATE OR REPLACE VIEW morning_ranks AS
SELECT * FROM (
    SELECT *, row_number() OVER (
        PARTITION BY CAST(run_ts AS DATE), symbol, risk_bucket
        ORDER BY run_ts
    ) AS rn
    FROM ranks
    WHERE run_kind = 'morning'
) WHERE rn = 1;
