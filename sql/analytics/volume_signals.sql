-- sql/analytics/05_volume_signals.sql

-- 16. Unusual volume — stocks trading significantly above average
SELECT
    f.ticker,
    s.company_name,
    f.trade_date,
    f.volume,
    ROUND(f.volume_ma_20)           AS avg_volume_20d,
    ROUND(f.volume_ratio, 2)        AS volume_ratio,
    ROUND(f.daily_return * 100, 2)  AS daily_return_pct,
    f.close
FROM FACT_PRICES f
JOIN DIM_STOCK s ON f.ticker = s.ticker
WHERE f.trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
  AND f.volume_ratio > 1.5
ORDER BY f.volume_ratio DESC;

-- 17. Year-over-year price comparison using LAG
SELECT
    ticker,
    trade_date,
    ROUND(close, 2)                                   AS current_close,
    ROUND(LAG(close, 252) OVER
        (PARTITION BY ticker ORDER BY trade_date), 2) AS close_1y_ago,
    ROUND((close - LAG(close, 252) OVER
        (PARTITION BY ticker ORDER BY trade_date))
        / NULLIF(LAG(close, 252) OVER
        (PARTITION BY ticker ORDER BY trade_date), 0) * 100, 2) AS yoy_return_pct
FROM FACT_PRICES
WHERE trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
ORDER BY yoy_return_pct DESC;

-- 18. Rolling 90-day return using window functions
SELECT
    ticker,
    trade_date,
    close,
    ROUND(close - LAG(close, 90) OVER
        (PARTITION BY ticker ORDER BY trade_date), 2)         AS price_change_90d,
    ROUND((close - LAG(close, 90) OVER
        (PARTITION BY ticker ORDER BY trade_date))
        / NULLIF(LAG(close, 90) OVER
        (PARTITION BY ticker ORDER BY trade_date), 0)*100, 2) AS return_90d_pct
FROM FACT_PRICES
WHERE ticker IN ('AAPL', 'MSFT', 'NVDA')
ORDER BY ticker, trade_date DESC
LIMIT 30;

-- 19. Stock ranking by multiple metrics — composite score
WITH latest AS (
    SELECT * FROM FACT_PRICES
    WHERE trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
      AND ma_200 IS NOT NULL
),
ranked AS (
    SELECT
        ticker,
        sector,
        close,
        PERCENT_RANK() OVER (ORDER BY cumulative_return)  AS return_rank,
        PERCENT_RANK() OVER (ORDER BY volatility_20 DESC) AS stability_rank,
        PERCENT_RANK() OVER (ORDER BY rsi_14)             AS rsi_rank,
        PERCENT_RANK() OVER (ORDER BY volume_ratio)       AS volume_rank
    FROM latest
)
SELECT
    r.ticker,
    s.company_name,
    r.sector,
    ROUND(r.close, 2)                                              AS close,
    ROUND((r.return_rank + r.stability_rank + r.volume_rank)/3*100, 1) AS composite_score,
    RANK() OVER (ORDER BY (r.return_rank + r.stability_rank + r.volume_rank) DESC) AS overall_rank
FROM ranked r
JOIN DIM_STOCK s ON r.ticker = s.ticker
ORDER BY overall_rank;

-- 20. Consecutive up/down days streak per stock
WITH streaks AS (
    SELECT
        ticker,
        trade_date,
        daily_return,
        SIGN(daily_return)                                         AS direction,
        SUM(CASE WHEN SIGN(daily_return) !=
            LAG(SIGN(daily_return)) OVER (PARTITION BY ticker ORDER BY trade_date)
            THEN 1 ELSE 0 END)
            OVER (PARTITION BY ticker ORDER BY trade_date)        AS streak_group
    FROM FACT_PRICES
    WHERE daily_return IS NOT NULL
),
streak_counts AS (
    SELECT
        ticker,
        direction,
        streak_group,
        COUNT(*) AS streak_length,
        MAX(trade_date) AS streak_end
    FROM streaks
    GROUP BY ticker, direction, streak_group
)
SELECT
    ticker,
    CASE WHEN direction = 1 THEN 'Up' ELSE 'Down' END AS direction,
    streak_length,
    streak_end
FROM streak_counts
WHERE (ticker, streak_end) IN (
    SELECT ticker, MAX(streak_end)
    FROM streak_counts
    GROUP BY ticker
)
ORDER BY streak_length DESC;