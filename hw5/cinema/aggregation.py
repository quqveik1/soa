import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic

import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from clickhouse_connect.driver.exceptions import ClickHouseError
from fastapi import FastAPI, HTTPException, Request

from cinema.storage import (
    connect_postgres, get_clickhouse, pending_exports, queue_export, save_postgres, upload_export,
)


log = logging.getLogger("uvicorn.error")
DB = Path("db")
DAILY_SQL = (DB / "daily.sql").read_text()
RETENTION_SQL = (DB / "retention.sql").read_text()
STORAGE_ERRORS = (ClickHouseError, psycopg.Error, BotoCoreError, ClientError)


class Aggregation:
    def __init__(self, clickhouse):
        self.clickhouse = clickhouse
        self.lock = Lock()

    def recalculate(self, day):
        with self.lock:
            started = monotonic()
            log.info("Aggregation started for %s", day)
            now = datetime.now(UTC)
            parameters = {"day": day}
            events, dau, average, conversion, movies = self.clickhouse.query(
                DAILY_SQL, parameters=parameters
            ).first_row
            retention = dict(self.clickhouse.query(
                RETENTION_SQL, parameters={**parameters, "today": now.date()}
            ).result_rows)
            metrics = {
                ("dau", ""): dau,
                ("avg_watch_seconds", ""): average,
                ("conversion", ""): conversion,
                **{("movie_views", movie): views for movie, views in movies.items()},
                **{("retention", str(offset)): retention.get(offset) for offset in range(8)},
            }
            computed_at = now.replace(microsecond=now.microsecond // 1000 * 1000)
            self.clickhouse.command("TRUNCATE TABLE cinema.daily_metrics_stage")
            self.clickhouse.insert(
                "cinema.daily_metrics_stage",
                [(day, metric, dim, value, computed_at) for (metric, dim), value in metrics.items()],
                column_names=["metric_date", "metric", "dimension", "value", "computed_at"],
            )
            self.clickhouse.command(
                "ALTER TABLE cinema.daily_metrics REPLACE PARTITION {day:Date32} "
                "FROM cinema.daily_metrics_stage", parameters=parameters,
            )
            save_postgres(day, metrics, computed_at, closed=day < now.date())
            log.info(
                "Aggregation completed for %s: %d events in %.2f s",
                day, events, monotonic() - started,
            )
            return {"date": day, "events": events, "metrics": len(metrics)}

    def export(self, day):
        with self.lock:
            with connect_postgres() as connection:
                if not queue_export(connection, day):
                    return None
            return upload_export(day)

    def run_cycle(self):
        today = datetime.now(UTC).date()
        try:
            with self.lock:
                active_days = self.clickhouse.query(
                    """
                    SELECT DISTINCT event_date
                    FROM cinema.events
                    WHERE event_date BETWEEN {first:Date32} AND {last:Date32}
                    """,
                    parameters={"first": today - timedelta(days=14), "last": today},
                ).result_rows
            days = {row[0] for row in active_days} | {today, today - timedelta(days=1)}
            for day in sorted(days, reverse=True):
                try:
                    self.recalculate(day)
                except STORAGE_ERRORS:
                    log.exception("Scheduled aggregation failed for %s", day)
        except ClickHouseError:
            log.exception("Could not read days to recalculate from ClickHouse")

        try:
            with self.lock:
                for day in pending_exports():
                    try:
                        upload_export(day)
                    except STORAGE_ERRORS:
                        log.exception("Export failed for %s, will retry next cycle", day)
                        break
        except psycopg.Error:
            log.exception("Could not read pending exports, will retry next cycle")


async def run_scheduler(aggregation, stop, interval):
    while not stop.is_set():
        await asyncio.to_thread(aggregation.run_cycle)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    with connect_postgres() as connection:
        connection.execute((DB / "postgres.sql").read_text())
    clickhouse = get_clickhouse()
    aggregation = Aggregation(clickhouse)
    app.state.aggregation = aggregation
    interval = max(1, int(os.getenv("AGGREGATION_INTERVAL_SECONDS", "30")))
    stop = asyncio.Event()
    scheduler = asyncio.create_task(run_scheduler(aggregation, stop, interval))
    try:
        yield
    finally:
        stop.set()
        await scheduler
        clickhouse.close()


app = FastAPI(title="Cinema Analytics", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/aggregate/{day}")
def aggregate(day: date, request: Request):
    if day.year < 1900 or day > datetime.now(UTC).date():
        raise HTTPException(422, "Use a date between 1900-01-01 and today (UTC)")
    try:
        return request.app.state.aggregation.recalculate(day)
    except STORAGE_ERRORS as exc:
        log.exception("Manual aggregation failed for %s", day)
        raise HTTPException(503, "Could not recalculate metrics") from exc


@app.post("/export/{day}")
def export(day: date, request: Request):
    try:
        path = request.app.state.aggregation.export(day)
    except STORAGE_ERRORS as exc:
        log.exception("Manual export failed for %s", day)
        raise HTTPException(503, "Could not export metrics") from exc
    if path is None:
        raise HTTPException(404, "Recalculate metrics for this date first")
    return {"path": path}
