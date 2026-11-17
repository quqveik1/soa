import json
import logging
import os
import struct
from concurrent.futures import Future
from datetime import UTC, datetime
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Annotated
from urllib.request import Request, urlopen
from uuid import UUID

from confluent_kafka import KafkaException, Producer
from fastavro import parse_schema, schemaless_writer
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator,
)

log = logging.getLogger("uvicorn.error")
TOPIC = "movie-events"
SUBJECT = f"{TOPIC}-value"
BOOTSTRAP_SERVERS = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
REGISTRY_URL = os.environ["SCHEMA_REGISTRY_URL"]
AVRO_SCHEMA = Path("schemas/movie-event-v1.avsc").read_text()
SCHEMA = parse_schema(json.loads(AVRO_SCHEMA))
DELIVERY_TIMEOUT_MS = 12_000
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EventType(StrEnum):
    VIEW_STARTED = "VIEW_STARTED"
    VIEW_FINISHED = "VIEW_FINISHED"
    VIEW_PAUSED = "VIEW_PAUSED"
    VIEW_RESUMED = "VIEW_RESUMED"
    LIKED = "LIKED"
    SEARCHED = "SEARCHED"


class DeviceType(StrEnum):
    MOBILE = "MOBILE"
    DESKTOP = "DESKTOP"
    TV = "TV"
    TABLET = "TABLET"


class MovieEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    user_id: Identifier
    movie_id: Identifier
    event_type: EventType
    timestamp: Annotated[AwareDatetime, Field(strict=True)]
    device_type: DeviceType
    session_id: Identifier
    progress_seconds: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)] | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, value):
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value):
        timestamp = value.astimezone(UTC)
        if not 1900 <= timestamp.year < 2300:
            raise ValueError("timestamp is outside the ClickHouse DateTime64 range")
        return timestamp.replace(microsecond=timestamp.microsecond // 1000 * 1000)

    @model_validator(mode="after")
    def check_progress(self):
        if self.event_type.startswith("VIEW_") and self.progress_seconds is None:
            raise ValueError("progress_seconds is required for view events")
        return self


def schema_request(suffix=""):
    request = Request(
        f"{REGISTRY_URL}/subjects/{SUBJECT}{suffix}",
        data=json.dumps({"schemaType": "AVRO", "schema": AVRO_SCHEMA}).encode(),
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


class KafkaPublisher:
    def __init__(self):
        schema_id = schema_request()["id"]
        self.header = struct.pack(">BI", 0, schema_id)
        self.producer = Producer({
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "enable.idempotence": True,
            "acks": "all",
            "retry.backoff.ms": 100,
            "retry.backoff.max.ms": 2000,
            "delivery.timeout.ms": DELIVERY_TIMEOUT_MS,
        })

    def publish(self, event: MovieEvent):
        message = BytesIO()
        message.write(self.header)
        schemaless_writer(message, SCHEMA, event.model_dump(), strict=True)

        delivery = Future()
        self.producer.produce(
            TOPIC,
            key=event.user_id.encode(),
            value=message.getvalue(),
            on_delivery=lambda error, _: delivery.set_result(error),
        )
        deadline = monotonic() + DELIVERY_TIMEOUT_MS / 1000 + 1
        while not delivery.done() and monotonic() < deadline:
            self.producer.poll(0.1)
        error = delivery.result(timeout=0)
        if error is not None:
            raise KafkaException(error)
        log.info(
            "Published event_id=%s event_type=%s timestamp=%s",
            event.event_id, event.event_type, event.timestamp.isoformat(),
        )
