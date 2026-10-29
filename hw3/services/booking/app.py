import time
from contextlib import asynccontextmanager
from datetime import date
from threading import Event, Thread
from typing import Annotated
from uuid import UUID, uuid4

import grpc
import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from google.protobuf.json_format import MessageToDict

from booking.client import FlightClient
from booking.models import BookingCreate, BookingResponse
from booking.service import BookingError, BookingService
from common.database import connect
from common.log import event, setup


@asynccontextmanager
async def lifespan(app):
    setup()
    client = FlightClient()
    service = BookingService(client)
    app.state.service = service
    stopped = Event()
    worker = Thread(target=service.recovery_loop, args=(stopped,), daemon=True)
    worker.start()
    yield
    stopped.set()
    worker.join(timeout=5)
    client.close()


app = FastAPI(title='Flight Booking', version='1.0.0', lifespan=lifespan)


def message_dict(message):
    return MessageToDict(message, preserving_proto_field_name=True,
                         always_print_fields_with_no_presence=True)


def error_response(status, code, message):
    return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message}})


@app.exception_handler(BookingError)
async def booking_error(request, error):
    return error_response(error.status, error.code, error.message)


@app.exception_handler(grpc.RpcError)
async def grpc_error(request, error):
    mapping = {
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.NOT_FOUND: 404,
        grpc.StatusCode.RESOURCE_EXHAUSTED: 409,
        grpc.StatusCode.FAILED_PRECONDITION: 409,
        grpc.StatusCode.ALREADY_EXISTS: 409,
        grpc.StatusCode.UNAVAILABLE: 503,
        grpc.StatusCode.DEADLINE_EXCEEDED: 503,
    }
    status = mapping.get(error.code(), 502)
    code = 'FLIGHT_UNAVAILABLE' if status == 503 else error.code().name
    message = error.details() if status != 502 else 'Flight Service returned an internal error'
    return error_response(status, code, message)


@app.exception_handler(psycopg.Error)
async def database_error(request, error):
    event('database_error', endpoint=request.url.path, error_type=type(error).__name__)
    return error_response(503, 'BOOKING_DATABASE_UNAVAILABLE', 'Booking database is unavailable')


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return error_response(400, 'VALIDATION_ERROR', 'Invalid request parameters')


@app.middleware('http')
async def request_log(request: Request, call_next):
    started, request_id = time.monotonic(), str(uuid4())
    response = await call_next(request)
    response.headers['X-Request-Id'] = request_id
    event('http_request', request_id=request_id, method=request.method, path=request.url.path,
          status=response.status_code, duration_ms=round((time.monotonic() - started) * 1000, 2))
    return response


@app.get('/health')
def health():
    with connect() as conn:
        conn.execute('SELECT 1')
    return {'status': 'ok'}


@app.get('/flights')
def search_flights(request: Request, origin: Annotated[str, Query(pattern='^[A-Z]{3}$')],
                   destination: Annotated[str, Query(pattern='^[A-Z]{3}$')], date: date | None = None):
    return message_dict(request.app.state.service.flight.search(
        origin, destination, date.isoformat() if date else ''))


@app.get('/flights/{flight_id}')
def get_flight(flight_id: UUID, request: Request):
    return message_dict(request.app.state.service.flight.get(flight_id))


@app.post('/bookings', status_code=201, response_model=BookingResponse)
def create_booking(body: BookingCreate, request: Request):
    return request.app.state.service.create(body)


@app.get('/bookings', response_model=list[BookingResponse])
def list_bookings(user_id: UUID, request: Request):
    return request.app.state.service.list(user_id)


@app.get('/bookings/{booking_id}', response_model=BookingResponse)
def get_booking(booking_id: UUID, request: Request):
    return request.app.state.service.get(booking_id)


@app.post('/bookings/{booking_id}/cancel', response_model=BookingResponse)
def cancel_booking(booking_id: UUID, request: Request):
    return request.app.state.service.cancel(booking_id)
