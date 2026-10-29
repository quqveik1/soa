import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.timestamp_pb2 import Timestamp

import flight_pb2 as pb
from common.database import connect
from flight.cache import advance_versions, flight_scope, route_scope


class FlightError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def reject(code, message):
    raise FlightError(code, message)


def uuid_value(value):
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        reject(grpc.StatusCode.INVALID_ARGUMENT, 'Expected a UUID')


def timestamp(value):
    result = Timestamp()
    result.FromDatetime(value)
    return result


def flight_message(row):
    return pb.Flight(
        id=str(row['id']), airline_code=row['airline_code'], airline_name=row['airline_name'],
        flight_number=row['flight_number'], origin=row['origin'], destination=row['destination'],
        departure_at=timestamp(row['departure_at']), arrival_at=timestamp(row['arrival_at']),
        total_seats=row['total_seats'], available_seats=row['available_seats'],
        price=str(row['price']), status=pb.FlightStatus.Value(row['status']),
    )


def reservation_message(row):
    return pb.SeatReservation(
        booking_id=str(row['booking_id']), flight_id=str(row['flight_id']),
        seat_count=row['seat_count'], status=pb.ReservationStatus.Value(row['status']),
        created_at=timestamp(row['created_at']),
    )


FLIGHT_SELECT = 'SELECT f.*, a.name AS airline_name FROM flights f JOIN airlines a ON a.code = f.airline_code'


def find_flight(conn, flight_id, lock=False):
    row = conn.execute(FLIGHT_SELECT + ' WHERE f.id = %s' + (' FOR UPDATE OF f' if lock else ''),
                       (flight_id,)).fetchone()
    if row is None:
        reject(grpc.StatusCode.NOT_FOUND, 'Flight not found')
    return row


def lock_booking(conn, booking_id):
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (str(booking_id),))


class FlightRepository:
    def __init__(self, cache):
        self.cache = cache

    def search(self, request):
        if (not re.fullmatch('[A-Z]{3}', request.origin)
                or not re.fullmatch('[A-Z]{3}', request.destination)
                or request.origin == request.destination):
            reject(grpc.StatusCode.INVALID_ARGUMENT, 'Expected different three-letter IATA codes')
        departure_date = None
        if request.date:
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', request.date):
                    raise ValueError()
                departure_date = date.fromisoformat(request.date)
                start = datetime.combine(departure_date, time(), tzinfo=timezone.utc)
                end = start + timedelta(days=1)
            except (ValueError, OverflowError):
                reject(grpc.StatusCode.INVALID_ARGUMENT, 'Expected date as YYYY-MM-DD')
        with connect() as conn:
            def load():
                sql = FLIGHT_SELECT + " WHERE f.origin = %s AND f.destination = %s AND f.status = 'SCHEDULED'"
                params = [request.origin, request.destination]
                if departure_date:
                    sql += ' AND f.departure_at >= %s AND f.departure_at < %s'
                    params.extend([start, end])
                rows = conn.execute(sql + ' ORDER BY f.departure_at, f.id', params).fetchall()
                return pb.SearchFlightsResponse(flights=[flight_message(row) for row in rows])
            scope = route_scope(request.origin, request.destination)
            return self.cache.load(conn, f'{scope}:{request.date or "all"}', scope,
                                   pb.SearchFlightsResponse, load)

    def get(self, request):
        flight_id = uuid_value(request.flight_id)
        with connect() as conn:
            key = flight_scope(flight_id)
            return self.cache.load(conn, key, key, pb.Flight,
                                   lambda: flight_message(find_flight(conn, flight_id)))

    def reserve(self, request):
        flight_id, booking_id = uuid_value(request.flight_id), uuid_value(request.booking_id)
        if not 1 <= request.seat_count <= 1000:
            reject(grpc.StatusCode.INVALID_ARGUMENT, 'seat_count must be between 1 and 1000')
        with connect() as conn:
            lock_booking(conn, booking_id)
            previous = conn.execute('SELECT * FROM seat_reservations WHERE booking_id = %s',
                                    (booking_id,)).fetchone()
            if previous:
                if previous['flight_id'] != flight_id or previous['seat_count'] != request.seat_count:
                    reject(grpc.StatusCode.ALREADY_EXISTS, 'booking_id already has different reservation parameters')
                if previous['status'] != 'ACTIVE':
                    reject(grpc.StatusCode.FAILED_PRECONDITION, 'Reservation has already been released')
                return reservation_message(previous)
            if conn.execute('SELECT 1 FROM reservation_cancellations WHERE booking_id = %s',
                            (booking_id,)).fetchone():
                reject(grpc.StatusCode.FAILED_PRECONDITION, 'Booking was already cancelled')
            flight = find_flight(conn, flight_id, lock=True)
            if flight['status'] != 'SCHEDULED':
                reject(grpc.StatusCode.FAILED_PRECONDITION, 'Flight is not scheduled')
            if flight['available_seats'] < request.seat_count:
                reject(grpc.StatusCode.RESOURCE_EXHAUSTED, 'Not enough available seats')
            conn.execute('UPDATE flights SET available_seats = available_seats - %s WHERE id = %s',
                         (request.seat_count, flight_id))
            reservation = conn.execute('''
                INSERT INTO seat_reservations(booking_id, flight_id, seat_count, status)
                VALUES (%s, %s, %s, 'ACTIVE') RETURNING *
            ''', (booking_id, flight_id, request.seat_count)).fetchone()
            advance_versions(conn, flight)
        self.cache.invalidate(flight)
        return reservation_message(reservation)

    def release(self, request):
        booking_id = uuid_value(request.booking_id)
        flight = None
        with connect() as conn:
            lock_booking(conn, booking_id)
            reservation = conn.execute('SELECT * FROM seat_reservations WHERE booking_id = %s FOR UPDATE',
                                       (booking_id,)).fetchone()
            if reservation is None:
                conn.execute('INSERT INTO reservation_cancellations(booking_id) VALUES (%s) ON CONFLICT DO NOTHING',
                             (booking_id,))
            elif reservation['status'] == 'ACTIVE':
                flight = find_flight(conn, reservation['flight_id'], lock=True)
                conn.execute('UPDATE flights SET available_seats = available_seats + %s WHERE id = %s',
                             (reservation['seat_count'], reservation['flight_id']))
                conn.execute("UPDATE seat_reservations SET status = 'RELEASED' WHERE booking_id = %s", (booking_id,))
                advance_versions(conn, flight)
        if flight:
            self.cache.invalidate(flight)
        return Empty()

    def update(self, request):
        flight_id = uuid_value(request.flight_id)
        if not any(request.HasField(field) for field in ('price', 'total_seats', 'status')):
            reject(grpc.StatusCode.INVALID_ARGUMENT, 'No fields to update')
        price = None
        if request.HasField('price'):
            try:
                price = Decimal(request.price)
                if not price.is_finite() or not 0 < price <= Decimal('9999999999.99') or price.as_tuple().exponent < -2:
                    raise ValueError()
            except (InvalidOperation, ValueError):
                reject(grpc.StatusCode.INVALID_ARGUMENT, 'Expected a positive price with at most two decimal places')
        if request.HasField('total_seats') and not 1 <= request.total_seats <= 1000:
            reject(grpc.StatusCode.INVALID_ARGUMENT, 'total_seats must be between 1 and 1000')
        if request.HasField('status') and request.status not in (pb.SCHEDULED, pb.DEPARTED, pb.CANCELLED, pb.COMPLETED):
            reject(grpc.StatusCode.INVALID_ARGUMENT, 'Invalid flight status')
        with connect() as conn:
            flight = find_flight(conn, flight_id, lock=True)
            reserved = flight['total_seats'] - flight['available_seats']
            capacity = request.total_seats if request.HasField('total_seats') else flight['total_seats']
            if capacity < reserved:
                reject(grpc.StatusCode.FAILED_PRECONDITION, 'Capacity cannot be smaller than reserved seats')
            conn.execute('UPDATE flights SET price = %s, total_seats = %s, available_seats = %s, status = %s WHERE id = %s',
                         (price if price is not None else flight['price'], capacity, capacity - reserved,
                          pb.FlightStatus.Name(request.status) if request.HasField('status') else flight['status'], flight_id))
            advance_versions(conn, flight)
            result = flight_message(find_flight(conn, flight_id))
        self.cache.invalidate(flight)
        return result
