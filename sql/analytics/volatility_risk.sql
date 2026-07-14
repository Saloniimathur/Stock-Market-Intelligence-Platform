-- sql/analytics/03_volatility_risk.sql

-- 9. Current volatility ranking across all stocks
SELECT
    f.ticker,
    s.company_name,
    s.sector,
    ROUND(f.volatility_20 * 100, 4)  AS volatility_20d_pct,
    ROUND(f.volatility_60 * 100, 4)  AS volatility_60d_pct,
    RANK() OVER (ORDER BY f.volatility_20 DESC) AS risk_rank,
    CASE
        WHEN f.volatility_20 > 0.025 THEN 'High risk'
        WHEN f.volatility_20 > 0.015 THEN 'Medium risk'
        ELSE 'Low risk'
    END                               AS risk_tier
FROM FACT_PRICES f
JOIN DIM_STOCK s ON f.ticker = s.ticker
WHERE f.trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
  AND f.volatility_20 IS NOT NULL
ORDER BY volatility_20d_pct DESC;

-- 10. Sharpe-style ratio — return per unit of risk (annualised)
WITH stats AS (
    SELECT
        ticker,
        AVG(daily_return)           AS avg_return,
        STDDEV(daily_return)        AS std_return,
        COUNT(*)                    AS trading_days
    FROM FACT_PRICES
    WHERE daily_return IS NOT NULL
    GROUP BY ticker
)
SELECT
    s.ticker,
    s2.company_name,
    s2.sector,
    ROUND(s.avg_return * 252 * 100, 2)              AS annualised_return_pct,
    ROUND(s.std_return * SQRT(252) * 100, 2)        AS annualised_volatility_pct,
    ROUND((s.avg_return * 252) /
          NULLIF(s.std_return * SQRT(252), 0), 3)   AS sharpe_ratio,
    s.trading_days
FROM stats s
JOIN DIM_STOCK s2 ON s.ticker = s2.ticker
ORDER BY sharpe_ratio DESC;

-- 11. Worst drawdown periods per stock
WITH drawdown AS (
    SELECT
        ticker,
        trade_date,
        close,
        MAX(close) OVER (
            PARTITION BY ticker
            ORDER BY trade_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                                AS running_max,
        (close - MAX(close) OVER (
            PARTITION BY ticker
            ORDER BY trade_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )) / NULLIF(MAX(close) OVER (
            PARTITION BY ticker
            ORDER BY trade_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ), 0)                                            AS drawdown_pct
    FROM FACT_PRICES
)
SELECT
    ticker,
    ROUND(MIN(drawdown_pct) * 100, 2) AS max_drawdown_pct,
    MIN(trade_date)                    AS period_start,
    MAX(trade_date)                    AS period_end
FROM drawdown
WHERE drawdown_pct = (
    SELECT MIN(d2.drawdown_pct)
    FROM drawdown d2
    WHERE d2.ticker = drawdown.ticker
)
GROUP BY ticker
ORDER BY max_drawdown_pct ASC;