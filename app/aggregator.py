import io
import json
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock, Thread

import psycopg
from flask import Flask
from minio import Minio
from minio.error import S3Error
from psycopg.rows import dict_row
from urllib3 import PoolManager

from clickhouse import query

app = Flask(__name__)
bucket = "movie-analytics"
cycle_lock = Lock()
pending_exports = set()


def rows(filename, day):
    sql = Path(__file__).with_name(filename).read_text()
    return [json.loads(line) for line in query(sql, day=day.isoformat()).splitlines()]


def connect():
    return psycopg.connect(os.environ["POSTGRES_DSN"], connect_timeout=5)


def save_postgres(day, computed_at, metrics):
    for attempt in range(3):
        try:
            with connect() as db, db.cursor() as cursor:
                cursor.execute("DELETE FROM daily_metrics WHERE day = %s", (day,))
                cursor.executemany(
                    "INSERT INTO daily_metrics (day, metric, dimension, value, computed_at) VALUES (%s, %s, %s, %s, %s)",
                    [(day, metric, dimension, value, computed_at) for metric, dimension, value in metrics],
                )
            return
        except psycopg.Error:
            logging.exception("PostgreSQL write for %s failed (attempt %d)", day, attempt + 1)
            if attempt == 2:
                raise
            time.sleep(2**attempt)


def retention(cohorts, cohort, offset):
    stats = cohorts.get(cohort)
    if stats is None or cohort + timedelta(days=offset) > datetime.now(UTC).date():
        return None
    return round(100 * int(stats["returned"][offset]) / int(stats["cohort_size"]), 2)


def aggregate(day):
    started = time.monotonic()
    logging.info("Aggregation started day=%s", day)
    daily, = rows("daily.sql", day)
    processed = int(daily.pop("events_processed"))
    daily["dau"] = int(daily["dau"])
    cohorts = {date.fromisoformat(row["cohort"]): row for row in rows("cohorts.sql", day)}
    metrics = [(metric, "", value) for metric, value in daily.items()]
    metrics += [(f"retention_d{offset}", "", retention(cohorts, day - timedelta(days=offset), offset))
                for offset in (1, 7)]
    metrics += [("cohort_retention", str(offset), retention(cohorts, day, offset)) for offset in range(8)]
    metrics += [("top_movie_views", row["movie_id"], int(row["views"])) for row in rows("top.sql", day)]
    computed_at = datetime.now(UTC)
    snapshot = {
        "day": day.isoformat(),
        "computed_at": computed_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
        "metrics": [dict(metric=metric, dimension=dimension, value=value)
                    for metric, dimension, value in metrics],
    }
    query("INSERT INTO cinema.daily_metrics FORMAT JSONEachRow", data=json.dumps(snapshot))
    save_postgres(day, computed_at, metrics)
    logging.info("Aggregation finished day=%s events=%d metrics=%d elapsed=%.2fs",
                 day, processed, len(metrics), time.monotonic() - started)
    return processed, metrics


def export(day):
    with connect() as db, db.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""
            SELECT day, metric, dimension, value, computed_at
            FROM daily_metrics WHERE day = %s ORDER BY metric, dimension
        """, (day,))
        metrics = cursor.fetchall()
    if not metrics:
        return None
    if not s3.bucket_exists(bucket):
        try:
            s3.make_bucket(bucket)
        except S3Error as error:
            if error.code != "BucketAlreadyOwnedByYou":
                raise
    body = json.dumps(metrics, default=lambda value: value.isoformat(), indent=2).encode()
    key = f"daily/{day}/aggregates.json"
    s3.put_object(bucket, key, io.BytesIO(body), len(body), content_type="application/json")
    logging.info("Exported %d metrics to s3://%s/%s", len(metrics), bucket, key)
    return f"s3://{bucket}/{key}"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/aggregate/<day>")
def recalculate(day):
    try:
        day = date.fromisoformat(day)
    except ValueError:
        return {"error": "Expected YYYY-MM-DD"}, 400
    try:
        with cycle_lock:
            processed, _ = aggregate(day)
    except Exception:
        logging.exception("Aggregation failed for %s", day)
        return {"error": "Aggregation failed"}, 503
    return {"day": day.isoformat(), "events_processed": processed}


@app.post("/export/<day>")
def export_date(day):
    try:
        day = date.fromisoformat(day)
    except ValueError:
        return {"error": "Expected YYYY-MM-DD"}, 400
    with cycle_lock:
        try:
            path = export(day)
        except Exception:
            logging.exception("Export failed for %s", day)
            pending_exports.add(day)
            return {"error": "Export failed, scheduled for retry"}, 503
        if path is None:
            return {"error": "No aggregates for this date"}, 404
        pending_exports.discard(day)
        return {"path": path}


def schedule(interval):
    last_daily_export = None
    while True:
        today = datetime.now(UTC).date()
        yesterday = today - timedelta(days=1)
        yesterday_snapshot = None
        for days_ago in range(14):
            day = today - timedelta(days=days_ago)
            with cycle_lock:
                try:
                    _, metrics = aggregate(day)
                    if day == yesterday:
                        yesterday_snapshot = (day, metrics)
                        if yesterday_snapshot != last_daily_export:
                            pending_exports.add(day)
                except Exception:
                    logging.exception("Aggregation failed for %s", day)
        with cycle_lock:
            for day in sorted(pending_exports):
                try:
                    if export(day) is not None:
                        pending_exports.remove(day)
                        if day == yesterday and yesterday_snapshot is not None:
                            last_daily_export = yesterday_snapshot
                except Exception:
                    logging.exception("Scheduled export failed for %s, will retry", day)
        time.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s3 = Minio(os.environ["S3_ENDPOINT"], access_key=os.environ["S3_ACCESS_KEY"],
               secret_key=os.environ["S3_SECRET_KEY"], secure=False, region="us-east-1",
               http_client=PoolManager(timeout=5, retries=False))
    interval = int(os.environ["AGGREGATION_INTERVAL_SECONDS"])
    Thread(target=schedule, args=(interval,), daemon=True).start()
    app.run(host="0.0.0.0", port=8000)
