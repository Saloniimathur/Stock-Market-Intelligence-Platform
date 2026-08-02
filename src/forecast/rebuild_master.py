import boto3
import pandas as pd
import io

s3 = boto3.client("s3", region_name="ap-south-1")
bucket = "stock-market-forecast-sm"

resp = s3.list_objects_v2(Bucket=bucket, Prefix="stocks/forecast/")
keys = [obj["Key"] for obj in resp.get("Contents", []) if obj["Key"].endswith("_forecast.csv")]
print(f"Found {len(keys)} forecast files")

dfs = []
for key in keys:
    obj = s3.get_object(Bucket=bucket, Key=key)
    df = pd.read_csv(io.BytesIO(obj["Body"].read()))
    dfs.append(df)

master = pd.concat(dfs, ignore_index=True)
ticker_count = master["ticker"].nunique()
print(f"Master forecast: {len(master):,} rows, {ticker_count} tickers")

master.to_csv("data/forecast/master_all_forecasts.csv", index=False)
print("Saved to data/forecast/master_all_forecasts.csv")

csv_buffer = io.StringIO()
master.to_csv(csv_buffer, index=False)
s3.put_object(
    Bucket=bucket,
    Key="stocks/forecast/master/all_forecasts.csv",
    Body=csv_buffer.getvalue(),
    ContentType="text/csv"
)
print("Also uploaded to S3 master folder")