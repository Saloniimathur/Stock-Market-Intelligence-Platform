-- sql/analytics/01_price_returns.sql

USE DATABASE STOCK_MARKET;
USE SCHEMA main;

-- 1. Latest closing price and YTD return for all stocks
SELECT
    f.ticker,
    s.company_name,
    s.sector,
    f.close                                          AS latest_close,
    f.trade_date                                     AS as_of_date,
    ROUND(f.cumulative_return * 100, 2)              AS ytd_return_pct,
    ROUND(f.daily_return * 100, 4)                   AS last_day_return_pct,
    ROUND(f.pct_from_52w_high * 100, 2)              AS pct_below_52w_high,
    f.high_52w,
    f.low_52w
FROM FACT_PRICES f
JOIN DIM_STOCK s ON f.ticker = s.ticker
WHERE f.trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
ORDER BY ytd_return_pct DESC;

-- 2. Top 5 best and worst performing stocks (last 30 trading days)
WITH recent AS (
    SELECT
        ticker,
        ROUND(SUM(daily_return) * 100, 2) AS total_return_30d
    FROM FACT_PRICES
    WHERE trade_date >= DATEADD(day, -30, (SELECT MAX(trade_date) FROM FACT_PRICES))
    GROUP BY ticker
)
SELECT
    ticker,
    total_return_30d,
    CASE WHEN total_return_30d > 0 THEN 'Gainer' ELSE 'Loser' END AS performance
FROM recent
ORDER BY total_return_30d DESC
LIMIT 10;

-- 3. Monthly average closing price per ticker
SELECT
    f.ticker,
    d.year,
    d.month_name,
    d.month,
    ROUND(AVG(f.close), 2)          AS avg_close,
    ROUND(MAX(f.close), 2)          AS month_high,
    ROUND(MIN(f.close), 2)          AS month_low,
    ROUND(AVG(f.daily_return)*100, 4) AS avg_daily_return_pct
FROM FACT_PRICES f
JOIN DIM_DATE d ON f.date_key = d.date_key
WHERE f.ticker = 'AAPL'
GROUP BY f.ticker, d.year, d.month, d.month_name
ORDER BY d.year, d.month;

-- 4. Daily return distribution — how often each stock gains vs loses
SELECT
    ticker,
    COUNT(*)                                                    AS total_days,
    SUM(CASE WHEN daily_return > 0 THEN 1 ELSE 0 END)          AS up_days,
    SUM(CASE WHEN daily_return < 0 THEN 1 ELSE 0 END)          AS down_days,
    ROUND(SUM(CASE WHEN daily_return > 0 THEN 1 ELSE 0 END)
          / COUNT(*) * 100, 1)                                  AS win_rate_pct,
    ROUND(AVG(daily_return) * 100, 4)                           AS avg_daily_return,
    ROUND(MAX(daily_return) * 100, 2)                           AS best_day_pct,
    ROUND(MIN(daily_return) * 100, 2)                           AS worst_day_pct
FROM FACT_PRICES
WHERE daily_return IS NOT NULL
GROUP BY ticker
ORDER BY win_rate_pct DESC;