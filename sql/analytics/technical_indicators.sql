-- sql/analytics/02_technical_indicators.sql

-- 5. Current RSI status — overbought (>70) or oversold (<30)
SELECT
    f.ticker,
    s.company_name,
    s.sector,
    ROUND(f.rsi_14, 2)      AS rsi,
    CASE
        WHEN f.rsi_14 >= 70 THEN 'Overbought'
        WHEN f.rsi_14 <= 30 THEN 'Oversold'
        ELSE 'Neutral'
    END                     AS rsi_signal,
    f.close,
    f.trade_date
FROM FACT_PRICES f
JOIN DIM_STOCK s ON f.ticker = s.ticker
WHERE f.trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
ORDER BY f.rsi_14 DESC;

-- 6. Moving average crossover status — is stock above or below MA50 and MA200?
SELECT
    f.ticker,
    s.sector,
    f.close,
    ROUND(f.ma_20, 2)   AS ma_20,
    ROUND(f.ma_50, 2)   AS ma_50,
    ROUND(f.ma_200, 2)  AS ma_200,
    CASE
        WHEN f.close > f.ma_50  AND f.close > f.ma_200  THEN 'Strong uptrend'
        WHEN f.close > f.ma_50  AND f.close < f.ma_200  THEN 'Short-term bullish'
        WHEN f.close < f.ma_50  AND f.close > f.ma_200  THEN 'Short-term bearish'
        WHEN f.close < f.ma_50  AND f.close < f.ma_200  THEN 'Strong downtrend'
        ELSE 'Transitioning'
    END                 AS trend_signal,
    f.trade_date
FROM FACT_PRICES f
JOIN DIM_STOCK s ON f.ticker = s.ticker
WHERE f.trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
  AND f.ma_200 IS NOT NULL
ORDER BY f.ticker;

-- 7. All golden cross and death cross events across history
SELECT
    f.ticker,
    s.company_name,
    f.trade_date,
    f.close,
    CASE
        WHEN f.golden_cross = 1 THEN 'Golden Cross'
        WHEN f.death_cross  = 1 THEN 'Death Cross'
    END AS signal_type,
    ROUND(f.ma_50,  2) AS ma_50,
    ROUND(f.ma_200, 2) AS ma_200
FROM FACT_PRICES f
JOIN DIM_STOCK s ON f.ticker = s.ticker
WHERE f.golden_cross = 1 OR f.death_cross = 1
ORDER BY f.trade_date DESC
LIMIT 50;

-- 8. Bollinger Band squeeze — low BB_width = low volatility = possible breakout
SELECT
    f.ticker,
    s.sector,
    f.trade_date,
    f.close,
    ROUND(f.bb_upper, 2)  AS bb_upper,
    ROUND(f.bb_lower, 2)  AS bb_lower,
    ROUND(f.bb_width, 4)  AS bb_width,
    CASE
        WHEN f.close > f.bb_upper THEN 'Above upper band'
        WHEN f.close < f.bb_lower THEN 'Below lower band'
        ELSE 'Within bands'
    END                   AS band_position
FROM FACT_PRICES f
JOIN DIM_STOCK s ON f.ticker = s.ticker
WHERE f.trade_date = (SELECT MAX(trade_date) FROM FACT_PRICES)
  AND f.bb_width IS NOT NULL
ORDER BY f.bb_width ASC;