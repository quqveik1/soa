import json
import logging
import os
from contextlib import closing
from datetime import UTC
from time import sleep

import boto3
import clickhouse_connect
import psycopg
from botocore.config import Config


log = logging.getLogger("uvicorn.error")
BUCKET = os.environ["S3_BUCKET"]


def get_clickhouse():
    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        connect_timeout=3,
        send_receive_timeout=20,
    )


def connect_postgres():
    return psycopg.connect(os.environ["POSTGRES_DSN"], connect_timeout=3)


def get_s3():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        region_name="us-east-1",
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )


def save_postgres(day, metrics, computed_at, closed):
    for attempt in range(3):
        try:
            with connect_postgres() as connection:
                existing = {
                    (metric, dimension): value
                    for metric, dimension, value in connection.execute(
                        "SELECT metric, dimension, value FROM daily_metrics WHERE metric_date = %s",
                        (day,),
                    )
                }
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO daily_metrics (metric_date, metric, dimension, value, computed_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (metric_date, metric, dimension) DO UPDATE
                        SET value = EXCLUDED.value, computed_at = EXCLUDED.computed_at
                        """,
                        [(day, metric, dimension, value, computed_at)
                         for (metric, dimension), value in metrics.items()],
                    )
                    cursor.executemany(
                        """
                        DELETE FROM daily_metrics
                        WHERE metric_date = %s AND metric = %s AND dimension = %s
                        """,
                        [(day, metric, dimension) for metric, dimension in existing.keys() - metrics.keys()],
                    )
                if closed:
                    queue_export(connection, day, refresh=existing != metrics)
            return
        except psycopg.OperationalError as exc:
            log.warning("PostgreSQL write for %s failed (attempt %d): %s", day, attempt + 1, exc)
            if attempt == 2:
                raise
            sleep(0.2 * 2**attempt)


def queue_export(connection, day, *, refresh=True):
    cursor = connection.execute(
        """
        INSERT INTO daily_exports (metric_date)
        SELECT %s WHERE EXISTS (SELECT 1 FROM daily_metrics WHERE metric_date = %s)
        ON CONFLICT (metric_date) DO UPDATE SET pending = TRUE WHERE %s
        """,
        (day, day, refresh),
    )
    return cursor.rowcount == 1


def pending_exports():
    with connect_postgres() as connection:
        return [row[0] for row in connection.execute(
            "SELECT metric_date FROM daily_exports WHERE pending ORDER BY metric_date"
        )]


def upload_export(day):
    with connect_postgres() as connection:
        rows = connection.execute(
            """
            SELECT metric, dimension, value, computed_at
            FROM daily_metrics WHERE metric_date = %s
            ORDER BY metric, dimension
            """,
            (day,),
        ).fetchall()
        document = {
            "date": day.isoformat(),
            "computed_at": rows[0][3].astimezone(UTC).isoformat(),
            "metrics": [
                {"metric": metric, "dimension": dimension, "value": value}
                for metric, dimension, value, _ in rows
            ],
        }
        key = f"daily/{day}/aggregates.json"
        with closing(get_s3()) as s3:
            s3.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=json.dumps(document, allow_nan=False).encode(),
                ContentType="application/json",
            )
        connection.execute(
            "UPDATE daily_exports SET pending = FALSE WHERE metric_date = %s", (day,)
        )
    path = f"s3://{BUCKET}/{key}"
    log.info("Exported %d metrics to %s", len(rows), path)
    return path
