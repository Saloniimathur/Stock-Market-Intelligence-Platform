import sys
import json
import io
import boto3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from tqdm import tqdm

sys.path.append(str(Path(__file__).resolve().parents[2]))
from src.utils import (load_config, load_aws_config, get_s3_client,
                       setup_logger, get_ticker_sector_map)

config     = load_config()
aws_config = load_aws_config()
logger     = setup_logger(config["etl"]["log_level"])

RAW_BUCKET      = aws_config["aws"]["buckets"]["raw"]
CLEAN_BUCKET    = aws_config["aws"]["buckets"]["clean"]
RUN_DATE        = datetime.now().strftime("%Y-%m-%d")

# ── Read from S3 ──────────────────────────────────────────────────────────────

def read_raw_from_s3(ticker: str, s3_client) -> pd.DataFrame:
    """Read today's raw JSON for a ticker from S3"""
    s3_key = f"stocks/raw/{ticker}/{RUN_DATE}.json"
    try:
        resp    = s3_client.get_object(Bucket=RAW_BUCKET, Key=s3_key)
        payload = json.loads(resp["Body"].read().decode("utf-8"))
        df      = pd.DataFrame(payload["data"])
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        logger.info(f"{ticker}: read {len(df):,} rows from S3 raw")
        return df
    except Exception as e:
        logger.error(f"{ticker}: failed to read from S3 — {e}")
        return None

# ── Technical indicators ──────────────────────────────────────────────────────

def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    df["MA_20"]  = df["Close"].rolling(window=20).mean().round(4)
    df["MA_50"]  = df["Close"].rolling(window=50).mean().round(4)
    df["MA_200"] = df["Close"].rolling(window=200).mean().round(4)
    return df

def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Relative Strength Index — momentum indicator 0-100"""
    delta  = df["Close"].diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_g  = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l  = loss.ewm(com=period - 1, min_periods=period).mean()
    rs     = avg_g / avg_l
    df["RSI_14"] = (100 - (100 / (1 + rs))).round(4)
    return df

def add_bollinger_bands(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Bollinger Bands — volatility envelope around MA"""
    ma    = df["Close"].rolling(window=period).mean()
    std   = df["Close"].rolling(window=period).std()
    df["BB_upper"] = (ma + 2 * std).round(4)
    df["BB_lower"] = (ma - 2 * std).round(4)
    df["BB_width"] = ((df["BB_upper"] - df["BB_lower"]) / ma).round(4)
    return df

def add_returns_and_volatility(df: pd.DataFrame) -> pd.DataFrame:
    """Daily returns and rolling volatility"""
    df["Daily_Return"]    = df["Close"].pct_change().round(6)
    df["Cumulative_Return"] = ((1 + df["Daily_Return"]).cumprod() - 1).round(6)
    df["Volatility_20"]   = df["Daily_Return"].rolling(window=20).std().round(6)
    df["Volatility_60"]   = df["Daily_Return"].rolling(window=60).std().round(6)
    return df

def add_golden_death_cross(df: pd.DataFrame) -> pd.DataFrame:
    """
    Golden cross: MA50 crosses above MA200 — bullish signal
    Death cross:  MA50 crosses below MA200 — bearish signal
    """
    df["Golden_Cross"] = (
        (df["MA_50"] > df["MA_200"]) &
        (df["MA_50"].shift(1) <= df["MA_200"].shift(1))
    ).astype(int)

    df["Death_Cross"] = (
        (df["MA_50"] < df["MA_200"]) &
        (df["MA_50"].shift(1) >= df["MA_200"].shift(1))
    ).astype(int)
    return df

def add_volume_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["Volume_MA_20"]  = df["Volume"].rolling(window=20).mean().round(0)
    df["Volume_Ratio"]  = (df["Volume"] / df["Volume_MA_20"]).round(4)
    return df

def add_price_levels(df: pd.DataFrame) -> pd.DataFrame:
    """52-week high/low — key reference levels traders watch"""
    df["High_52W"] = df["High"].rolling(window=252).max().round(4)
    df["Low_52W"]  = df["Low"].rolling(window=252).min().round(4)
    df["Pct_From_52W_High"] = ((df["Close"] - df["High_52W"]) / df["High_52W"]).round(4)
    return df

# ── Full transform pipeline ───────────────────────────────────────────────────

def transform_ticker(df: pd.DataFrame, ticker: str, sector: str) -> pd.DataFrame:
    """Apply all transformations to one ticker's dataframe"""

    # Add identifiers
    df["Ticker"] = ticker
    df["Sector"] = sector

    # Apply all indicators in sequence
    df = add_moving_averages(df)
    df = add_rsi(df)
    df = add_bollinger_bands(df)
    df = add_returns_and_volatility(df)
    df = add_golden_death_cross(df)
    df = add_volume_indicators(df)
    df = add_price_levels(df)

    # Drop rows where core indicators aren't yet calculable
    # (first 200 rows lack MA_200 — keep them with NaN, Snowflake handles it)
    df["Date"] = pd.to_datetime(df["Date"])

    # Final column order
    cols = [
        "Date", "Ticker", "Sector",
        "Open", "High", "Low", "Close", "Volume",
        "Daily_Return", "Cumulative_Return",
        "MA_20", "MA_50", "MA_200",
        "RSI_14",
        "BB_upper", "BB_lower", "BB_width",
        "Volatility_20", "Volatility_60",
        "Golden_Cross", "Death_Cross",
        "Volume_MA_20", "Volume_Ratio",
        "High_52W", "Low_52W", "Pct_From_52W_High"
    ]
    df = df[[c for c in cols if c in df.columns]]

    logger.info(f"{ticker}: transformed — {len(df):,} rows, {len(df.columns)} columns")
    return df

# ── Write to S3 clean as Parquet ─────────────────────────────────────────────

def write_clean_to_s3(ticker: str, df: pd.DataFrame, s3_client) -> str:
    """Write cleaned dataframe as Parquet to S3 clean zone"""
    s3_key = f"stocks/clean/{ticker}.parquet"

    # Write parquet to in-memory buffer
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, engine="pyarrow")
    buffer.seek(0)

    s3_client.put_object(
        Bucket=CLEAN_BUCKET,
        Key=s3_key,
        Body=buffer.getvalue(),
        ContentType="application/octet-stream",
        Metadata={
            "ticker":     ticker,
            "row_count":  str(len(df)),
            "columns":    str(len(df.columns)),
            "run_date":   RUN_DATE
        }
    )
    logger.info(f"{ticker}: written to s3://{CLEAN_BUCKET}/{s3_key}")
    return s3_key

def verify_clean(ticker: str, s3_client) -> bool:
    s3_key = f"stocks/clean/{ticker}.parquet"
    try:
        resp    = s3_client.head_object(Bucket=CLEAN_BUCKET, Key=s3_key)
        size_kb = resp["ContentLength"] / 1024
        logger.info(f"{ticker}: verified in S3 clean — {size_kb:.1f} KB")
        return True
    except Exception as e:
        logger.error(f"{ticker}: clean verification failed — {e}")
        return False

# ── Main ──────────────────────────────────────────────────────────────────────

def run_transform():
    logger.info("=" * 60)
    logger.info("TRANSFORM — S3 raw → S3 clean")
    logger.info(f"Source bucket: {RAW_BUCKET}")
    logger.info(f"Target bucket: {CLEAN_BUCKET}")
    logger.info("=" * 60)

    s3_client  = get_s3_client()
    sector_map = get_ticker_sector_map(config)
    tickers    = list(sector_map.keys())

    results    = {"success": [], "failed": []}
    all_dfs    = []

    for ticker in tqdm(tickers, desc="Transforming stocks"):
        df = read_raw_from_s3(ticker, s3_client)
        if df is None:
            results["failed"].append(ticker)
            continue

        df_clean = transform_ticker(df, ticker, sector_map[ticker])
        s3_key   = write_clean_to_s3(ticker, df_clean, s3_client)
        verified = verify_clean(ticker, s3_client)

        if verified:
            results["success"].append(ticker)
            all_dfs.append(df_clean)
        else:
            results["failed"].append(ticker)

    # Also write a combined master parquet for easy Snowflake loading
    if all_dfs:
        logger.info("Writing combined master parquet...")
        master_df = pd.concat(all_dfs, ignore_index=True)

        buffer = io.BytesIO()
        master_df.to_parquet(buffer, index=False, engine="pyarrow")
        buffer.seek(0)

        s3_client.put_object(
            Bucket=CLEAN_BUCKET,
            Key="stocks/clean/master/all_stocks.parquet",
            Body=buffer.getvalue(),
            ContentType="application/octet-stream",
            Metadata={"row_count": str(len(master_df)), "run_date": RUN_DATE}
        )
        logger.info(f"Master parquet: {len(master_df):,} rows across {len(all_dfs)} tickers")

    logger.info("=" * 60)
    logger.info("TRANSFORM COMPLETE")
    logger.info(f"Successful : {len(results['success'])}")
    logger.info(f"Failed     : {len(results['failed'])} — {results['failed']}")
    logger.info("=" * 60)

    return results, master_df if all_dfs else None

if __name__ == "__main__":
    results, master_df = run_transform()

    if master_df is not None:
        print(f"\nMaster dataframe shape: {master_df.shape}")
        print(f"Date range: {master_df['Date'].min()} to {master_df['Date'].max()}")
        print(f"Tickers: {master_df['Ticker'].nunique()}")
        print(f"Sectors: {master_df['Sector'].unique()}")
        print(f"\nSample — AAPL last 3 rows:")
        print(master_df[master_df['Ticker']=='AAPL'].tail(3)[
            ['Date','Close','MA_50','MA_200','RSI_14','Daily_Return','Golden_Cross']
        ].to_string())