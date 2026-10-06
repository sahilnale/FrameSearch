"""Initialize only the browser demo's fresh private bucket and Kafka topic."""

import os
import time

import boto3
import psycopg
from botocore.config import Config
from confluent_kafka.admin import AdminClient, NewTopic

deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=2
        ) as connection:
            connection.execute("SELECT id FROM videos LIMIT 1")
        break
    except psycopg.Error:
        time.sleep(1)
else:
    raise SystemExit("Browser demo database initialization timed out")

storage = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_INTERNAL"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    region_name="us-east-1",
    config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}),
)
while time.monotonic() < deadline:
    try:
        storage.create_bucket(Bucket=os.environ["S3_BUCKET"])
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("Browser demo storage initialization timed out")

admin = AdminClient({"bootstrap.servers": os.environ["KAFKA_BROKERS"], "log_level": 0})
while time.monotonic() < deadline:
    try:
        if admin.list_topics(timeout=2).brokers:
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit("Browser demo broker startup timed out")
for future in admin.create_topics([NewTopic(os.environ["KAFKA_TOPIC"], 1, 1)]).values():
    future.result(timeout=20)
print(
    "Fresh browser demo dependencies initialized; no videos or frames seeded",
    flush=True,
)
