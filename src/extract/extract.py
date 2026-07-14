import sys
import json
import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime
from tqdm import tqdm

sys.path.append(str(Path(__file__).resolve().parents[2]))
from src.utils import load_config, load_aws_config, get_s3_client, setup_logger, get_all_tickers, get_ticker_sector_map

config     = load_config()
aws_config = load_aws_config()
logger     = setup_logger(config["etl"]["log_level"])

S3_RAW_BUCKET = aws_config["aws"]["buckets"]["raw"]
START_DATE    = config["etl"]["start_date"]
END_DATE      = config["etl"]["end_date"]
RUN_DATE      = datetime.now().strftime("%Y-%m-%d")

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str) -> pd.DataFrame:
    """Pull full OHLCV history for one ticker from yfinance"""
    try:
        tkr = yf.Ticker(ticker)
        df  = tkr.history(
            start=START_DATE,
            end=END_DATE,
            auto_adjust=True,
            raise_errors=False
        )

        if df is None or df.empty:
            logger.warning(f"{ticker}: no data returned")
            return None

        df = df.reset_index()

        # Flatten multi-index columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        # Normalize Date column — remove timezone info for clean storage
        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
        df["Date"] = df["Date"].astype(str)

        # Keep only the columns we need
        keep = ["Date", "Open", "High", "Low", "Close", "Volume"]
        df   = df[[c for c in keep if c in df.columns]]
        df["Ticker"] = ticker

        logger.info(f"{ticker}: {len(df):,} rows fetched")
        return df

    except Exception as e:
        logger.error(f"{ticker}: fetch failed — {e}")
        return None

def upload_to_s3_raw(ticker: str, df: pd.DataFrame, s3_client) -> str:
    """Upload ticker data as JSON to S3 raw zone"""
    records = df.to_dict(orient="records")
    payload = {
        "ticker":      ticker,
        "extracted_at": RUN_DATE,
        "start_date":  START_DATE,
        "end_date":    END_DATE,
        "row_count":   len(records),
        "data":        records
    }

    s3_key = f"stocks/raw/{ticker}/{RUN_DATE}.json"

    s3_client.put_object(
        Bucket=S3_RAW_BUCKET,
        Key=s3_key,
        Body=json.dumps(payload, default=str),
        ContentType="application/json",
        Metadata={
            "ticker":     ticker,
            "row_count":  str(len(records)),
            "run_date":   RUN_DATE
        }
    )
    logger.info(f"{ticker}: uploaded to s3://{S3_RAW_BUCKET}/{s3_key}")
    return s3_key

def verify_upload(ticker: str, s3_client) -> bool:
    """Confirm the file actually landed in S3"""
    s3_key = f"stocks/raw/{ticker}/{RUN_DATE}.json"
    try:
        resp = s3_client.head_object(Bucket=S3_RAW_BUCKET, Key=s3_key)
        size_kb = resp["ContentLength"] / 1024
        logger.info(f"{ticker}: verified in S3 — {size_kb:.1f} KB")
        return True
    except Exception as e:
        logger.error(f"{ticker}: verification failed — {e}")
        return False

# ── Main ──────────────────────────────────────────────────────────────────────

def run_extract():
    logger.info("=" * 60)
    logger.info("EXTRACT — yfinance → S3 raw")
    logger.info(f"Date range: {START_DATE} to {END_DATE}")
    logger.info(f"Target bucket: {S3_RAW_BUCKET}")
    logger.info("=" * 60)

    s3_client   = get_s3_client()
    tickers     = get_all_tickers(config)
    sector_map  = get_ticker_sector_map(config)

    results = {
        "success": [],
        "failed":  [],
        "skipped": []
    }

    for ticker in tqdm(tickers, desc="Extracting stocks"):
        logger.info(f"Processing {ticker} ({sector_map[ticker]})")

        df = fetch_ohlcv(ticker)
        if df is None:
            results["failed"].append(ticker)
            continue

        s3_key = upload_to_s3_raw(ticker, df, s3_client)
        verified = verify_upload(ticker, s3_client)

        if verified:
            results["success"].append(ticker)
        else:
            results["failed"].append(ticker)

    # Summary
    logger.info("=" * 60)
    logger.info("EXTRACT COMPLETE")
    logger.info(f"Successful : {len(results['success'])} — {results['success']}")
    logger.info(f"Failed     : {len(results['failed'])} — {results['failed']}")
    logger.info("=" * 60)

    return results

if __name__ == "__main__":
    results = run_extract()

    # Print S3 inventory after extract
    s3_client = get_s3_client()
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(
        Bucket=S3_RAW_BUCKET,
        Prefix=f"stocks/raw/"
    )
    total_files = sum(1 for page in pages for _ in page.get("Contents", []))
    print(f"\nS3 raw bucket now contains {total_files} files")