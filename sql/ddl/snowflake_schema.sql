USE DATABASE STOCK_MARKET;
USE SCHEMA   main;

--Dimension Tables
CREATE OR REPLACE TABLE DIM_STOCK (
    stock_key       INTEGER       PRIMARY KEY AUTOINCREMENT,
    ticker          VARCHAR(10)   NOT NULL UNIQUE,
    company_name    VARCHAR(200),
    sector          VARCHAR(100),
    market_cap_band VARCHAR(20),   -- Large / Mid / Small
    created_at      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TABLE DIM_SECTOR (
    sector_key      INTEGER       PRIMARY KEY AUTOINCREMENT,
    sector_name     VARCHAR(100)  NOT NULL UNIQUE,
    description     VARCHAR(500),
    created_at      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TABLE DIM_DATE (
    date_key        INTEGER       PRIMARY KEY,  -- YYYYMMDD
    full_date       DATE          NOT NULL,
    day             INTEGER,
    day_of_week     VARCHAR(20),
    week            INTEGER,
    month           INTEGER,
    month_name      VARCHAR(20),
    quarter         INTEGER,
    year            INTEGER,
    is_weekend      BOOLEAN,
    is_market_holiday BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- ── Fact Tables ───────────────────────────────────────────────────────────────


CREATE OR REPLACE TABLE FACT_PRICES (
    price_id            INTEGER       PRIMARY KEY AUTOINCREMENT,
    date_key            INTEGER       REFERENCES DIM_DATE(date_key),
    trade_date          DATE,                        -- renamed from date
    ticker              VARCHAR(10)   NOT NULL,
    sector              VARCHAR(100),
    open                FLOAT,
    high                FLOAT,
    low                 FLOAT,
    close               FLOAT,
    volume              BIGINT,
    daily_return        FLOAT,
    cumulative_return   FLOAT,
    ma_20               FLOAT,
    ma_50               FLOAT,
    ma_200              FLOAT,
    rsi_14              FLOAT,
    bb_upper            FLOAT,
    bb_lower            FLOAT,
    bb_width            FLOAT,
    volatility_20       FLOAT,
    volatility_60       FLOAT,
    golden_cross        INTEGER,
    death_cross         INTEGER,
    volume_ma_20        FLOAT,
    volume_ratio        FLOAT,
    high_52w            FLOAT,
    low_52w             FLOAT,
    pct_from_52w_high   FLOAT,
    created_at          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (ticker, date_key);

CREATE OR REPLACE TABLE FACT_FORECASTS (
    forecast_id         INTEGER       PRIMARY KEY AUTOINCREMENT,
    ticker              VARCHAR(10)   NOT NULL,
    forecast_date       DATE          NOT NULL,
    forecast_period     INTEGER,       -- 30, 60, or 90
    predicted_close     FLOAT,
    lower_bound         FLOAT,
    upper_bound         FLOAT,
    trend               FLOAT,
    created_at          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

SHOW TABLES IN SCHEMA main;

-- Row count check
SELECT 'DIM_STOCK'   AS tbl, COUNT(*) AS row_count FROM DIM_STOCK   UNION ALL
SELECT 'DIM_SECTOR',         COUNT(*)          FROM DIM_SECTOR  UNION ALL
SELECT 'DIM_DATE',           COUNT(*)          FROM DIM_DATE    UNION ALL
SELECT 'FACT_PRICES',        COUNT(*)          FROM FACT_PRICES
ORDER BY tbl;

-- Quick sanity join — top 5 trading days by volume
SELECT
    f.ticker,
    d.full_date,
    f.close,
    f.volume,
    f.daily_return,
    f.rsi_14
FROM FACT_PRICES f
JOIN DIM_DATE d ON f.date_key = d.date_key
WHERE f.ticker = 'AAPL'
ORDER BY f.volume DESC
LIMIT 5;