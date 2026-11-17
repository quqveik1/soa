import json
import os
import time
import unittest
from contextlib import closing
from datetime import UTC, date, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from cinema.storage import connect_postgres, get_clickhouse, get_s3


MOVIE_URL = os.getenv("MOVIE_URL", "http://movie:8000")
AGGREGATION_URL = os.getenv("AGGREGATION_URL", "http://aggregation:8000")
REGISTRY_URL = os.getenv("SCHEMA_REGISTRY_URL", "http://schema-registry:8081")


def get_json(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=35) as response:
        return json.load(response)


def event(user_id, movie_id, timestamp, kind, session_id, progress=None):
    return {
        "event_id": str(uuid4()),
        "user_id": user_id,
        "movie_id": movie_id,
        "timestamp": timestamp,
        "event_type": kind,
        "device_type": "TV",
        "session_id": session_id,
        "progress_seconds": progress,
    }


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clickhouse = get_clickhouse()
        cls.addClassCleanup(cls.clickhouse.close)

    def wait_for_event(self, event_id):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            result = self.clickhouse.query(
                """
                SELECT event_id, user_id, movie_id, event_type, toString(timestamp) AS timestamp,
                       device_type, session_id, progress_seconds
                FROM cinema.events FINAL WHERE event_id = {id:UUID}
                """,
                parameters={"id": event_id},
            )
            row = next(result.named_results(), None)
            if row is not None:
                row["event_id"] = str(row["event_id"])
                return row
            time.sleep(0.5)
        self.fail(f"ClickHouse did not receive {event_id}")

    def test_http_to_clickhouse(self):
        payload = event(
            str(uuid4()), "arrival", "2026-04-07T15:46:20.123+03:00",
            "VIEW_FINISHED", str(uuid4()), 6840,
        )
        for invalid in (
            {"progress_seconds": -1}, {"progress_seconds": None},
            {"timestamp": "2026-04-07T15:46:20"}, {"timestamp": 1_775_536_780},
            {"timestamp": "1775536780"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(HTTPError) as error:
                    get_json(f"{MOVIE_URL}/events", {**payload, **invalid})
                self.assertEqual(error.exception.code, 422)
        self.assertEqual(get_json(f"{MOVIE_URL}/events", payload)["event_id"], payload["event_id"])
        self.assertEqual(
            self.wait_for_event(payload["event_id"]),
            {**payload, "timestamp": "2026-04-07 12:46:20.123"},
        )

    def test_registered_schema(self):
        registered = get_json(f"{REGISTRY_URL}/subjects/movie-events-value/versions/1")
        self.assertEqual(registered["version"], 1)
        self.assertEqual(json.loads(registered["schema"])["name"], "MovieEvent")

    def test_daily_metrics_and_export(self):
        rows = self.clickhouse.query(
            "SELECT DISTINCT event_date FROM cinema.events"
        ).result_rows
        occupied = {row[0] for row in rows}
        day = date(2020, 1, 1)
        while any(day + timedelta(days=offset) in occupied for offset in range(8)):
            day += timedelta(days=8)

        user_a, user_b, session = (str(uuid4()) for _ in range(3))
        events = [
            event(user_a, "arrival", f"{day}T11:00:00Z", "VIEW_STARTED", session, 0),
            event(user_a, "arrival", f"{day}T12:00:00Z", "VIEW_FINISHED", session, 3600),
            event(user_b, "arrival", f"{day}T14:00:00Z", "VIEW_STARTED", str(uuid4()), 0),
            event(user_a, "arrival", f"{day + timedelta(days=1)}T11:00:00Z", "SEARCHED", str(uuid4())),
            event(user_b, "arrival", f"{day + timedelta(days=7)}T11:00:00Z", "LIKED", str(uuid4())),
        ]
        for payload in [*events, events[1]]:
            get_json(f"{MOVIE_URL}/events", payload)
        for payload in events:
            self.wait_for_event(payload["event_id"])

        for _ in range(2):
            result = get_json(f"{AGGREGATION_URL}/aggregate/{day}", {})
            self.assertEqual(result["events"], 3)

        expected = {
            ("dau", ""): 2,
            ("avg_watch_seconds", ""): 3600,
            ("conversion", ""): 0.5,
            ("movie_views", "arrival"): 2,
            **{("retention", str(offset)): rate for offset, rate in enumerate(
                [1, 0.5, 0, 0, 0, 0, 0, 0.5]
            )},
        }
        with connect_postgres() as connection:
            pg_rows = connection.execute(
                """
                SELECT metric, dimension, value, computed_at
                FROM daily_metrics WHERE metric_date = %s
                """,
                (day,),
            ).fetchall()
        self.assertEqual(len(pg_rows), len(expected))
        self.assertEqual({(metric, dim): value for metric, dim, value, _ in pg_rows}, expected)
        ch_rows = self.clickhouse.query(
            """
            SELECT metric, dimension, value
            FROM cinema.daily_metrics WHERE metric_date = {day:Date32}
            """,
            parameters={"day": day},
        ).result_rows
        self.assertEqual(len(ch_rows), len(expected))
        self.assertEqual({(metric, dim): value for metric, dim, value in ch_rows}, expected)

        for _ in range(2):
            get_json(f"{AGGREGATION_URL}/export/{day}", {})
        key = f"daily/{day}/aggregates.json"
        with closing(get_s3()) as s3:
            with s3.get_object(Bucket="movie-analytics", Key=key)["Body"] as body:
                document = json.load(body)
            objects = s3.list_objects_v2(Bucket="movie-analytics", Prefix=f"daily/{day}/")
        self.assertEqual(document["date"], day.isoformat())
        self.assertEqual(document["computed_at"], pg_rows[0][3].astimezone(UTC).isoformat())
        self.assertEqual({
            (row["metric"], row["dimension"]): row["value"] for row in document["metrics"]
        }, expected)
        self.assertEqual([item["Key"] for item in objects["Contents"]], [key])
