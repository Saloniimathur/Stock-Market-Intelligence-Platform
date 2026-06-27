import sys
import io
import yaml
import boto3
import snowflake.connector
from pathlib import Path
from loguru import logger
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)

def load_aws_config(path: str = "config/aws_config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)

def get_s3_client():
    """boto3 picks up credentials from ~/.aws/credentials automatically"""
    return boto3.client("s3", region_name=load_aws_config()["aws"]["region"])

def get_s3_resource():
    return boto3.resource("s3", region_name=load_aws_config()["aws"]["region"])

def get_snowflake_connection(config: dict):
    sf = config["snowflake"]
    conn = snowflake.connector.connect(
        account=sf["account"],
        user=sf["user"],
        password=sf["password"],
        role=sf["role"],
        warehouse=sf["warehouse"],
        database=sf["database"],
        schema=sf["schema"],
    )
    logger.info("Snowflake connection established")
    return conn

def setup_logger(log_level: str = "INFO"):
    Path("logs").mkdir(exist_ok=True)
    logger.remove()
    logger.add(
        f"logs/pipeline_{datetime.now().strftime('%Y%m%d')}.log",
        rotation="10 MB",
        level=log_level,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
    )
    def safe_print(msg):
        print(msg.encode("ascii", errors="replace").decode("ascii"), end="")
    logger.add(safe_print, level=log_level)
    return logger

def get_all_tickers(config: dict) -> list:
    """Flatten all tickers from all sectors into a single list"""
    tickers = []
    for sector, stocks in config["stocks"].items():
        tickers.extend(stocks)
    return tickers

def get_ticker_sector_map(config: dict) -> dict:
    """Returns {ticker: sector} mapping"""
    mapping = {}
    for sector, stocks in config["stocks"].items():
        for ticker in stocks:
            mapping[ticker] = sector
    return mapping