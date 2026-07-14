import sys
import io
import json
import boto3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from snowflake.connector.pandas_tools import write_pandas

sys.path.append(str(Path(__file__).resolve().parents[2]))
from src.utils import (load_config, load_aws_config, get_s3_client,
                       get_snowflake_connection, setup_logger,
                       get_ticker_sector_map)

config     = load_config()
aws_config = load_aws_config()
logger     = setup_logger(config["etl"]["log_level"])

CLEAN_BUCKET = aws_config["aws"]["buckets"]["clean"]

# Company name mapping — enriches DIM_STOCK beyond what yfinance gives us
COMPANY_NAMES = {
    "AAPL": ("Apple Inc.",             "Large"), "MSFT": ("Microsoft Corp.",       "Large"),
    "NVDA": ("NVIDIA Corp.",           "Large"), "GOOGL": ("Alphabet Inc.",        "Large"),
    "META": ("Meta Platforms Inc.",    "Large"), "JPM":  ("JPMorgan Chase & Co.", "Large"),
    "GS":   ("Goldman Sachs Group",   "Large"), "BAC":  ("Bank of America Corp.", "Large"),
    "MS":   ("Morgan Stanley",        "Large"), "WFC":  ("Wells Fargo & Co.",     "Large"),
    "JNJ":  ("Johnson & Johnson",     "Large"), "PFE":  ("Pfizer Inc.",           "Large"),
    "UNH":  ("UnitedHealth Group",    "Large"), "ABBV": ("AbbVie Inc.",           "Large"),
    "MRK":  ("Merck & Co. Inc.",      "Large"), "XOM":  ("Exxon Mobil Corp.",     "Large"),
    "CVX":  ("Chevron Corp.",         "Large"), "COP":  ("ConocoPhillips",        "Large"),
    "SLB":  ("SLB (Schlumberger)",    "Large"), "EOG":  ("EOG Resources Inc.",    "Large"),
    "AMZN": ("Amazon.com Inc.",       "Large"), "WMT":  ("Walmart Inc.",          "Large"),
    "HD":   ("Home Depot Inc.",       "Large"), "MCD":  ("McDonald's Corp.",      "Large"),
    "NKE":  ("Nike Inc.",             "Large"),
}

SECTOR_DESCRIPTIONS = {
    "Technology":  "Companies in software, hardware, semiconductors, and IT services",
    "Finance":     "Banks, investment firms, insurance, and financial services",
    "Healthcare":  "Pharmaceuticals, medical devices, health insurance, and biotech",
    "Energy":      "Oil, gas, renewable energy, and energy services companies",
    "Consumer":    "Retail, e-commerce, restaurants, and consumer goods companies",
}

# ── Read master parquet from S3 ───────────────────────────────────────────────

def read_master_from_s3(s3_client) -> pd.DataFrame:
    logger.info("Reading master parquet from S3 clean...")
    resp   = s3_client.get_object(
        Bucket=CLEAN_BUCKET,
        Key="stocks/clean/master/all_stocks.parquet"
    )
    buffer = io.BytesIO(resp["Body"].read())
    df     = pd.read_parquet(buffer, engine="pyarrow")
    logger.info(f"Master parquet loaded: {len(df):,} rows, {df.shape[1]} columns")
    return df

# ── Build dimension tables ────────────────────────────────────────────────────

def build_dim_stock(df: pd.DataFrame) -> pd.DataFrame:
    sector_map = get_ticker_sector_map(config)
    records = []
    for ticker, sector in sector_map.items():
        name, cap_band = COMPANY_NAMES.get(ticker, (ticker, "Large"))
        records.append({
            "TICKER":          ticker,
            "COMPANY_NAME":    name,
            "SECTOR":          sector,
            "MARKET_CAP_BAND": cap_band
        })
    dim = pd.DataFrame(records)
    logger.info(f"DIM_STOCK: {len(dim)} rows")
    return dim

def build_dim_sector() -> pd.DataFrame:
    records = [
        {"SECTOR_NAME": s, "DESCRIPTION": d}
        for s, d in SECTOR_DESCRIPTIONS.items()
    ]
    dim = pd.DataFrame(records)
    logger.info(f"DIM_SECTOR: {len(dim)} rows")
    return dim

def build_dim_date(df: pd.DataFrame) -> pd.DataFrame:
    dates = df["Date"].drop_duplicates().dropna()
    dates = pd.to_datetime(dates)

    dim = pd.DataFrame({
        "DATE_KEY":    dates.dt.strftime("%Y%m%d").astype(int),
        "FULL_DATE":   dates.dt.date,
        "DAY":         dates.dt.day,
        "DAY_OF_WEEK": dates.dt.day_name(),
        "WEEK":        dates.dt.isocalendar().week.astype(int),
        "MONTH":       dates.dt.month,
        "MONTH_NAME":  dates.dt.month_name(),
        "QUARTER":     dates.dt.quarter,
        "YEAR":        dates.dt.year,
        "IS_WEEKEND":  dates.dt.dayofweek >= 5,
    })
    dim = dim.drop_duplicates(subset=["DATE_KEY"]).sort_values("DATE_KEY")
    logger.info(f"DIM_DATE: {len(dim)} rows")
    return dim

# ── Build fact table ──────────────────────────────────────────────────────────

def build_fact_prices(df: pd.DataFrame) -> pd.DataFrame:
    fact = df.copy()

    # Build date key before any renaming
    fact["DATE_KEY"] = pd.to_datetime(fact["Date"]).dt.strftime("%Y%m%d").astype(int)

    col_map = {
        "Date": "TRADE_DATE",
        "Ticker": "TICKER", "Sector": "SECTOR",
        "Open": "OPEN", "High": "HIGH", "Low": "LOW",
        "Close": "CLOSE", "Volume": "VOLUME",
        "Daily_Return": "DAILY_RETURN",
        "Cumulative_Return": "CUMULATIVE_RETURN",
        "MA_20": "MA_20", "MA_50": "MA_50", "MA_200": "MA_200",
        "RSI_14": "RSI_14",
        "BB_upper": "BB_UPPER", "BB_lower": "BB_LOWER", "BB_width": "BB_WIDTH",
        "Volatility_20": "VOLATILITY_20", "Volatility_60": "VOLATILITY_60",
        "Golden_Cross": "GOLDEN_CROSS", "Death_Cross": "DEATH_CROSS",
        "Volume_MA_20": "VOLUME_MA_20", "Volume_Ratio": "VOLUME_RATIO",
        "High_52W": "HIGH_52W", "Low_52W": "LOW_52W",
        "Pct_From_52W_High": "PCT_FROM_52W_HIGH",
    }
    fact = fact.rename(columns=col_map)

    # Cast TRADE_DATE to plain Python date — Snowflake rejects nanosecond timestamps
    fact["TRADE_DATE"] = pd.to_datetime(fact["TRADE_DATE"]).dt.date

    # Keep only mapped columns + DATE_KEY
    keep = list(col_map.values()) + ["DATE_KEY"]
    fact = fact[[c for c in keep if c in fact.columns]]

    # Replace NaN with None for Snowflake
    fact = fact.replace({np.nan: None})

    logger.info(f"FACT_PRICES: {len(fact):,} rows")
    return fact

# ── Load to Snowflake ─────────────────────────────────────────────────────────

def load_table(conn, df: pd.DataFrame, table: str, truncate: bool = True):
    sf = config["snowflake"]
    if truncate:
        conn.cursor().execute(f"TRUNCATE TABLE IF EXISTS {table}")
        logger.info(f"Truncated {table}")

    success, chunks, rows, _ = write_pandas(
        conn=conn,
        df=df,
        table_name=table,
        database=sf["database"],
        schema=sf["schema"],
        chunk_size=config["etl"]["batch_size"],
        auto_create_table=False,
        overwrite=False,
    )
    if success:
        logger.info(f"Loaded {table}: {rows:,} rows in {chunks} chunks")
    else:
        raise RuntimeError(f"write_pandas failed for {table}")

def verify_counts(conn):
    logger.info("Verifying Snowflake row counts...")
    tables = ["DIM_STOCK", "DIM_SECTOR", "DIM_DATE", "FACT_PRICES"]
    cur = conn.cursor()
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        logger.info(f"  {table:20s} {count:>10,} rows")

# ── Main ──────────────────────────────────────────────────────────────────────

def run_load():
    logger.info("=" * 60)
    logger.info("LOAD — S3 clean → Snowflake")
    logger.info("=" * 60)

    s3_client = get_s3_client()
    conn      = get_snowflake_connection(config)

    try:
        # Read master parquet from S3
        master_df = read_master_from_s3(s3_client)

        # Build all tables
        dim_stock  = build_dim_stock(master_df)
        dim_sector = build_dim_sector()
        dim_date   = build_dim_date(master_df)
        fact_prices = build_fact_prices(master_df)

        # Load in order — dims first, facts second
        load_table(conn, dim_stock,   "DIM_STOCK")
        load_table(conn, dim_sector,  "DIM_SECTOR")
        load_table(conn, dim_date,    "DIM_DATE")
        load_table(conn, fact_prices, "FACT_PRICES")

        verify_counts(conn)

        logger.info("=" * 60)
        logger.info("LOAD COMPLETE")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Load failed: {e}")
        raise
    finally:
        conn.close()
        logger.info("Snowflake connection closed")

if __name__ == "__main__":
    run_load()