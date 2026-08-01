import sys
import io
import warnings
import boto3
import pandas as pd
import numpy as np
import pmdarima as pm
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm

warnings.filterwarnings("ignore")

sys.path.append(str(Path(__file__).resolve().parents[2]))
from src.utils import (load_config, load_aws_config, get_s3_client,
                       get_snowflake_connection, setup_logger,
                       get_ticker_sector_map)

config     = load_config()
aws_config = load_aws_config()
logger     = setup_logger(config["etl"]["log_level"])

CLEAN_BUCKET      = aws_config["aws"]["buckets"]["clean"]
FORECAST_BUCKET   = aws_config["aws"]["buckets"]["forecast"]
FORECAST_PERIODS  = config["forecast"]["periods"]   # [30, 60, 90]

# ── Read clean data from S3 ───────────────────────────────────────────────────

def read_ticker_from_s3(ticker: str, s3_client) -> pd.DataFrame:
    s3_key = f"stocks/clean/{ticker}.parquet"
    try:
        resp   = s3_client.get_object(Bucket=CLEAN_BUCKET, Key=s3_key)
        buffer = io.BytesIO(resp["Body"].read())
        df     = pd.read_parquet(buffer, engine="pyarrow")
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"{ticker}: failed to read from S3 — {e}")
        return None

# ── ARIMA model ────────────────────────────────────────────────────────────────

def fit_auto_arima(series: pd.Series):
    """
    auto_arima automatically finds the best (p,d,q) parameters by testing
    combinations and picking the one with lowest AIC (model fit quality score).

    p = how many past values the model looks at (autoregressive term)
    d = how many times we difference the series to make it stationary
    q = how many past forecast errors the model corrects for (moving average term)

    seasonal=False because stock daily closes don't have strong fixed-period
    seasonality the way retail sales or web traffic do.
    """
    model = pm.auto_arima(
        series,
        start_p=1, start_q=1,
        max_p=5, max_q=5,
        d=None,                    # let auto_arima determine differencing order
        seasonal=False,
        stepwise=True,             # faster search — doesn't try every combination
        suppress_warnings=True,
        error_action="ignore",
        trace=False
    )
    return model

def run_arima_forecast(ticker: str, df: pd.DataFrame, periods: int) -> pd.DataFrame:
    """
    Fit ARIMA on closing price series and forecast N business days ahead.
    Returns a dataframe matching the FACT_FORECASTS schema.
    """
    df = df.dropna(subset=["Close"]).sort_values("Date")
    close_series = df["Close"].reset_index(drop=True)
    last_date    = df["Date"].max()
    last_close   = close_series.iloc[-1]

    # Fit model
    model = fit_auto_arima(close_series)

    # Forecast with confidence intervals (95% by default)
    forecast, conf_int = model.predict(
        n_periods=periods,
        return_conf_int=True,
        alpha=0.05          # 95% confidence interval
    )

    # Build future business day dates
    future_dates = pd.bdate_range(
        start=last_date + timedelta(days=1),
        periods=periods
    )

    result = pd.DataFrame({
        "forecast_date":   future_dates.date,
        "predicted_close": np.round(forecast, 4),
        "lower_bound":     np.round(np.clip(conf_int[:, 0], 0, None), 4),
        "upper_bound":     np.round(conf_int[:, 1], 4),
        "trend":           np.round(forecast, 4)   # ARIMA forecast IS the trend line here
    })

    result["ticker"]          = ticker
    result["forecast_period"] = periods

    logger.info(f"{ticker}: {periods}d forecast (order={model.order}) — "
                f"last close ${last_close:.2f} → "
                f"predicted ${result['predicted_close'].iloc[-1]:.2f} "
                f"(range ${result['lower_bound'].iloc[-1]:.2f}"
                f"–${result['upper_bound'].iloc[-1]:.2f})")

    return result

# ── Save forecast to S3 ───────────────────────────────────────────────────────

def save_forecast_to_s3(ticker: str, forecast_df: pd.DataFrame, s3_client):
    s3_key = f"stocks/forecast/{ticker}_forecast.csv"
    csv_buffer = io.StringIO()
    forecast_df.to_csv(csv_buffer, index=False)

    s3_client.put_object(
        Bucket=FORECAST_BUCKET,
        Key=s3_key,
        Body=csv_buffer.getvalue(),
        ContentType="text/csv",
        Metadata={
            "ticker":   ticker,
            "run_date": datetime.now().strftime("%Y-%m-%d")
        }
    )
    logger.info(f"{ticker}: forecast saved to s3://{FORECAST_BUCKET}/{s3_key}")

def load_forecasts_to_snowflake(all_forecasts: pd.DataFrame, conn):
    from snowflake.connector.pandas_tools import write_pandas
    sf = config["snowflake"]

    load_df = all_forecasts[[
        "ticker", "forecast_date", "forecast_period",
        "predicted_close", "lower_bound", "upper_bound", "trend"
    ]].copy()

    load_df.columns = [c.upper() for c in load_df.columns]
    load_df["FORECAST_DATE"] = pd.to_datetime(load_df["FORECAST_DATE"]).dt.date
    load_df = load_df.replace({np.nan: None})

    conn.cursor().execute("TRUNCATE TABLE IF EXISTS FACT_FORECASTS")
    logger.info("Truncated FACT_FORECASTS")

    success, chunks, rows, _ = write_pandas(
        conn=conn,
        df=load_df,
        table_name="FACT_FORECASTS",
        database=sf["database"],
        schema=sf["schema"],
        chunk_size=10000,
        auto_create_table=False,
        overwrite=False,
    )

    if success:
        logger.info(f"FACT_FORECASTS loaded: {rows:,} rows in {chunks} chunks")
    else:
        raise RuntimeError("write_pandas failed for FACT_FORECASTS")

# ── Main ──────────────────────────────────────────────────────────────────────

def run_forecast():
    logger.info("=" * 60)
    logger.info("FORECAST — ARIMA time-series forecasting")
    logger.info(f"Periods: {FORECAST_PERIODS} days")
    logger.info("=" * 60)

    s3_client  = get_s3_client()
    sector_map = get_ticker_sector_map(config)
    tickers    = list(sector_map.keys())

    all_forecasts = []
    results       = {"success": [], "failed": []}

    for ticker in tqdm(tickers, desc="Forecasting stocks"):
        try:
            df = read_ticker_from_s3(ticker, s3_client)
            if df is None:
                results["failed"].append(ticker)
                continue

            ticker_forecasts = []
            for periods in FORECAST_PERIODS:
                forecast_df = run_arima_forecast(ticker, df, periods)
                ticker_forecasts.append(forecast_df)

            combined = pd.concat(ticker_forecasts, ignore_index=True)
            all_forecasts.append(combined)

            save_forecast_to_s3(ticker, combined, s3_client)
            results["success"].append(ticker)

        except Exception as e:
            logger.error(f"{ticker}: forecast failed — {e}")
            results["failed"].append(ticker)
            continue

    if all_forecasts:
        master_forecast = pd.concat(all_forecasts, ignore_index=True)
        logger.info(f"Total forecast rows: {len(master_forecast):,}")

        conn = get_snowflake_connection(config)
        try:
            load_forecasts_to_snowflake(master_forecast, conn)
        finally:
            conn.close()

        buffer = io.StringIO()
        master_forecast.to_csv(buffer, index=False)
        s3_client.put_object(
            Bucket=FORECAST_BUCKET,
            Key="stocks/forecast/master/all_forecasts.csv",
            Body=buffer.getvalue(),
            ContentType="text/csv"
        )
        logger.info("Master forecast saved to S3")

    logger.info("=" * 60)
    logger.info("FORECAST COMPLETE")
    logger.info(f"Successful : {len(results['success'])} — {results['success']}")
    logger.info(f"Failed     : {len(results['failed'])} — {results['failed']}")
    logger.info("=" * 60)

    return master_forecast if all_forecasts else None

if __name__ == "__main__":
    master = run_forecast()

    if master is not None:
        print(f"\nForecast summary:")
        print(f"Total rows     : {len(master):,}")
        print(f"Tickers        : {master['ticker'].nunique()}")
        print(f"Periods        : {sorted(master['forecast_period'].unique())}")
        print(f"\nSample — AAPL 30-day forecast (first 5 rows):")
        sample = master[
            (master["ticker"] == "AAPL") &
            (master["forecast_period"] == 30)
        ].head(5)
        print(sample[["forecast_date","predicted_close",
                       "lower_bound","upper_bound"]].to_string(index=False))