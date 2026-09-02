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
    ts        TIMESTAMP NOT NULL,   -- bar OPEN time, America/Chicago, tz-naive
    symbol    VARCHAR   NOT NULL,
    interval  VARCHAR   NOT NULL,   -- '1min' | '5min' | '1hour'
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
    session   VARCHAR,              -- 'pre' | 'regular' | 'post'. Without this a
                                    -- 19:45 print is indistinguishable from a
                                    -- 10:45 one and H11 is unrunnable.
    provider  VARCHAR NOT NULL,
    PRIMARY KEY (ts, symbol, interval, provider)
);
ALTER TABLE prices_intraday ADD COLUMN IF NOT EXISTS session VARCHAR;

-- Which tickers can actually be traded outside regular hours, per provider.
-- H11 selects on this: an extended-hours strategy on an ineligible ticker is
-- not a strategy, it is a fill that never happens.
CREATE TABLE IF NOT EXISTS symbol_venue (
    symbol                  VARCHAR NOT NULL,
    extended_hours_eligible BOOLEAN,
    provider                VARCHAR NOT NULL,
    as_of                   DATE    NOT NULL,
    PRIMARY KEY (symbol, provider, as_of)
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

-- The information timestamp. RULE 5: execution and backtest logic keys off
-- `available_at`, never `run_ts`.
--
-- This is a VIEW, not columns on `ranks`, because populating a new column on
-- 1.25M existing rows would be an UPDATE and rule 2 forbids that. Derived from
-- ranks.first_seen_sha JOIN harvest_manifest.committed_at, so it cannot drift
-- from the manifest and costs nothing to recompute.
--
--   stamp_is_forward = run_ts > committed_at. Upstream stamps some builds with
--   a time LATER than it published them (FINDINGS F4). Those rows are not a
--   trap to exclude -- a rank published at 19:36 CT is actionable in extended
--   hours ~13h before the next regular open (see H11). The flag flags; it does
--   not forbid.
--
--   availability_source says how much to trust available_at:
--     'committed_at'  forward-stamped: committed_at IS the availability, exact.
--     'run_ts'        normal row: run_ts is the build time and upstream
--                     publishes promptly, so run_ts is the estimate and
--                     committed_at is only a (possibly months-late) upper bound
--                     because the coverage walk reads 2-4 blobs, not all 405.
--                     `harvest_lag_days` exposes how loose that bound is.
CREATE OR REPLACE VIEW ranks_pit AS
SELECT r.*,
       m.committed_at,
       (r.run_ts > m.committed_at)                      AS stamp_is_forward,
       CASE WHEN r.run_ts > m.committed_at
            THEN m.committed_at ELSE r.run_ts END       AS available_at,
       CASE WHEN r.run_ts > m.committed_at
            THEN 'committed_at' ELSE 'run_ts' END       AS availability_source,
       date_diff('day', r.run_ts, m.committed_at)       AS harvest_lag_days
FROM ranks r
LEFT JOIN (SELECT DISTINCT commit_sha, committed_at FROM harvest_manifest) m
       ON m.commit_sha = r.first_seen_sha;
