-- sql/analytics/04_sector_analysis.sql

-- 12. Sector YTD performance comparison
SELECT
    f.sector,
    ROUND(AVG(f.cumulative_return) * 100, 2)  AS avg_ytd_return_pct,
    ROUND(MAX(f.cumulative_return) * 100, 2)  AS best_stock_return_pct,
    ROUND(MIN(f.cumulative_return) * 100, 2)  AS worst_stock_return_pct,
    ROUND(AVG(f.volatility_20) * 100, 4)      AS avg_volatility_pct,
    COUNT(DISTINCT f.ticker)                   AS stock_count
FROM FACT_PRICES f
WHERE f.trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
GROUP BY f.sector
ORDER BY avg_ytd_return_pct DESC;

-- 13. Sector average RSI — which sectors are overbought/oversold?
SELECT
    f.sector,
    ROUND(AVG(f.rsi_14), 2)   AS avg_rsi,
    ROUND(MIN(f.rsi_14), 2)   AS min_rsi,
    ROUND(MAX(f.rsi_14), 2)   AS max_rsi,
    CASE
        WHEN AVG(f.rsi_14) >= 70 THEN 'Overbought'
        WHEN AVG(f.rsi_14) <= 30 THEN 'Oversold'
        ELSE 'Neutral'
    END                        AS sector_rsi_signal
FROM FACT_PRICES f
WHERE f.trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
  AND f.rsi_14 IS NOT NULL
GROUP BY f.sector
ORDER BY avg_rsi DESC;

-- 14. Monthly sector returns — which sector wins each month?
WITH monthly_returns AS (
    SELECT
        f.sector,
        d.year,
        d.month,
        d.month_name,
        ROUND(AVG(f.daily_return) * 21 * 100, 2) AS monthly_return_pct
    FROM FACT_PRICES f
    JOIN DIM_DATE d ON f.date_key = d.date_key
    WHERE f.daily_return IS NOT NULL
    GROUP BY f.sector, d.year, d.month, d.month_name
),
ranked AS (
    SELECT *,
        RANK() OVER (PARTITION BY year, month ORDER BY monthly_return_pct DESC) AS rank
    FROM monthly_returns
)
SELECT year, month, month_name, sector, monthly_return_pct
FROM ranked
WHERE rank = 1
ORDER BY year, month;

-- 15. Sector correlation — do sectors move together?
WITH daily_sector AS (
    SELECT
        trade_date,
        sector,
        AVG(daily_return) AS avg_sector_return
    FROM FACT_PRICES
    WHERE daily_return IS NOT NULL
    GROUP BY trade_date, sector
),
pivoted AS (
    SELECT
        trade_date,
        AVG(CASE WHEN sector = 'Technology'  THEN avg_sector_return END) AS tech,
        AVG(CASE WHEN sector = 'Finance'     THEN avg_sector_return END) AS finance,
        AVG(CASE WHEN sector = 'Healthcare'  THEN avg_sector_return END) AS healthcare,
        AVG(CASE WHEN sector = 'Energy'      THEN avg_sector_return END) AS energy,
        AVG(CASE WHEN sector = 'Consumer'    THEN avg_sector_return END) AS consumer
    FROM daily_sector
    GROUP BY trade_date
)
SELECT
    ROUND(CORR(tech, finance),     3) AS tech_vs_finance,
    ROUND(CORR(tech, healthcare),  3) AS tech_vs_healthcare,
    ROUND(CORR(tech, energy),      3) AS tech_vs_energy,
    ROUND(CORR(tech, consumer),    3) AS tech_vs_consumer,
    ROUND(CORR(finance, energy),   3) AS finance_vs_energy,
    ROUND(CORR(healthcare, energy),3) AS healthcare_vs_energy
FROM pivoted;