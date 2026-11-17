import logging
from contextlib import asynccontextmanager

from confluent_kafka import KafkaException
from fastapi import FastAPI, HTTPException, Request

from cinema.events import KafkaPublisher, MovieEvent


log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    publisher = KafkaPublisher()
    app.state.publisher = publisher
    try:
        yield
    finally:
        publisher.producer.flush(1)


app = FastAPI(title="Movie Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/events", status_code=201)
def publish_event(event: MovieEvent, request: Request):
    try:
        request.app.state.publisher.publish(event)
    except (KafkaException, BufferError, TimeoutError) as exc:
        log.warning("Kafka publication failed for %s: %s", event.event_id, exc)
        raise HTTPException(503, "Kafka did not acknowledge the event") from exc
    return {"event_id": event.event_id}
