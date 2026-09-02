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
    build_stamp    TIMESTAMP,          -- from the FILENAME, where the source has
                                       -- one file per build (daily_ranks/). NULL
                                       -- for production/*_latest.pkl, which is
                                       -- rewritten in place and carries no stamp.
    PRIMARY KEY (file_path, commit_sha)
);
ALTER TABLE harvest_manifest ADD COLUMN IF NOT EXISTS build_stamp TIMESTAMP;

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
-- ranks.first_seen_sha JOIN harvest_manifest, so it cannot drift.
--
--   available_at = min(build_stamp, committed_at, run_ts)
--
-- Each is an UPPER BOUND on when the run was public, so the earliest is the
-- tightest truth, and min() needs no fallback chain and no special-casing.
-- run_ts joins the min() on the strength of the step-2 audit: for morning,
-- intraday and placeholder the newest `Date` equals the filename build stamp
-- EXACTLY (delta 0.00h), so on those rows run_ts IS the build stamp. Dropping it
-- would fall back to committed_at for every production-sourced row, which the
-- 2-blob coverage walk pins 21-292 days late.
-- Neither source alone is safe, because nightly uses two stamping conventions
-- (FINDINGS F4):
--   * filename honest, Date +24h  -> build_stamp is right, and it beats
--     committed_at, which carries push lag.
--   * filename AND Date +24h      -> build_stamp is ~4.4h LATE (it would date
--     all_low_risk_PROD_20260902_000000 to 09-02 00:00, after the extended-hours
--     session closes, silently deleting the window H11 exists to test).
--     committed_at (09-01 19:36) is right and wins.
-- min() resolves all four observed cases without a special case.
--
--   stamp_is_forward = run_ts > available_at. Flags the nightly retrain; it does
--   NOT forbid the row. A rank published 19:36 CT is actionable in extended
--   hours ~13h before the next regular open (H11).
--
--   availability_source records WHICH bound won, so the distribution is
--   inspectable rather than buried: 'build_stamp' | 'committed_at' | 'run_ts'
--   (the last where no manifest row matches).
CREATE OR REPLACE VIEW ranks_pit AS
WITH prov AS (
    SELECT commit_sha,
           min(build_stamp)  AS build_stamp,
           min(committed_at) AS committed_at
    FROM harvest_manifest GROUP BY commit_sha
)
SELECT r.*,
       p.committed_at,
       p.build_stamp,
       least(coalesce(p.build_stamp,  r.run_ts),
             coalesce(p.committed_at, r.run_ts),
             r.run_ts)                                       AS available_at,
       CASE
         WHEN p.build_stamp IS NOT NULL
              AND p.build_stamp <= coalesce(p.committed_at, p.build_stamp)
              AND p.build_stamp <= r.run_ts               THEN 'build_stamp'
         WHEN p.committed_at IS NOT NULL
              AND p.committed_at <= r.run_ts              THEN 'committed_at'
         ELSE 'run_ts'
       END                                                   AS availability_source,
       r.run_ts > least(coalesce(p.build_stamp,  r.run_ts),
                        coalesce(p.committed_at, r.run_ts),
                        r.run_ts)                            AS stamp_is_forward,
       date_diff('day', r.run_ts, p.committed_at)            AS harvest_lag_days
FROM ranks r
LEFT JOIN prov p ON p.commit_sha = r.first_seen_sha;
