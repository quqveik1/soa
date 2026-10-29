from decimal import Decimal
from uuid import uuid4

from common.database import connect
from common.log import event


class BookingError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def find_booking(conn, booking_id, lock=False):
    row = conn.execute('SELECT * FROM bookings WHERE id = %s' + (' FOR UPDATE' if lock else ''),
                       (booking_id,)).fetchone()
    if row is None:
        raise BookingError(404, 'BOOKING_NOT_FOUND', 'Booking not found')
    return row


def delete_operation(conn, booking_id, kind):
    conn.execute('DELETE FROM pending_operations WHERE booking_id = %s AND kind = %s', (booking_id, kind))


class BookingService:
    def __init__(self, flight_client):
        self.flight = flight_client

    def create(self, body):
        flight = self.flight.get(body.flight_id)
        total_price = Decimal(flight.price) * body.seat_count
        booking_id = uuid4()
        with connect() as conn:
            conn.execute("INSERT INTO pending_operations(booking_id, kind) VALUES (%s, 'CREATE')", (booking_id,))
        try:
            with connect() as conn:
                operation = conn.execute('''
                    SELECT * FROM pending_operations WHERE booking_id = %s AND kind = 'CREATE' FOR UPDATE
                ''', (booking_id,)).fetchone()
                if operation is None:
                    raise BookingError(503, 'BOOKING_ABORTED', 'Booking attempt was already compensated; try again')
                self.flight.reserve(body.flight_id, body.seat_count, booking_id)
                booking = conn.execute('''
                    INSERT INTO bookings(id, user_id, flight_id, passenger_name, passenger_email,
                                         seat_count, total_price, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'CONFIRMED') RETURNING *
                ''', (booking_id, body.user_id, body.flight_id, body.passenger_name,
                      str(body.passenger_email), body.seat_count, total_price)).fetchone()
                delete_operation(conn, booking_id, 'CREATE')
            return booking
        except Exception:
            try:
                self.recover_one(booking_id, 'CREATE')
            except Exception as error:
                event('compensation_pending', booking_id=booking_id, error_type=type(error).__name__)
            raise

    def get(self, booking_id):
        with connect() as conn:
            return find_booking(conn, booking_id)

    def list(self, user_id):
        with connect() as conn:
            return conn.execute('SELECT * FROM bookings WHERE user_id = %s ORDER BY created_at DESC, id',
                                (user_id,)).fetchall()

    def cancel(self, booking_id):
        with connect() as conn:
            booking = find_booking(conn, booking_id, lock=True)
            if booking['status'] != 'CONFIRMED':
                raise BookingError(409, 'BOOKING_ALREADY_CANCELLED', 'Booking has already been cancelled')
            inserted = conn.execute('''
                INSERT INTO pending_operations(booking_id, kind) VALUES (%s, 'CANCEL')
                ON CONFLICT DO NOTHING RETURNING booking_id
            ''', (booking_id,)).fetchone()
            if inserted is None:
                raise BookingError(409, 'CANCELLATION_PENDING', 'Cancellation is already in progress')
        self.recover_one(booking_id, 'CANCEL')
        return self.get(booking_id)

    def finish_operation(self, conn, operation):
        booking_id, kind = operation['booking_id'], operation['kind']
        booking = conn.execute('SELECT * FROM bookings WHERE id = %s FOR UPDATE', (booking_id,)).fetchone()
        if kind == 'CREATE':
            if booking is None:
                self.flight.release(booking_id)
                event('reservation_compensated', booking_id=booking_id)
        elif booking is not None and booking['status'] == 'CONFIRMED':
            self.flight.release(booking_id)
            conn.execute("UPDATE bookings SET status = 'CANCELLED' WHERE id = %s", (booking_id,))
            event('cancellation_completed', booking_id=booking_id)
        delete_operation(conn, booking_id, kind)

    def recover_one(self, booking_id=None, kind=None):
        with connect() as conn:
            if booking_id is None:
                operation = conn.execute('''
                    SELECT * FROM pending_operations
                    WHERE created_at < clock_timestamp() - interval '5 seconds'
                    ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
                ''').fetchone()
            else:
                operation = conn.execute('''
                    SELECT * FROM pending_operations WHERE booking_id = %s AND kind = %s FOR UPDATE
                ''', (booking_id, kind)).fetchone()
            if operation is None:
                return False
            self.finish_operation(conn, operation)
            return True

    def recovery_loop(self, stopped):
        while not stopped.is_set():
            try:
                self.recover_one()
            except Exception as error:
                event('recovery_delayed', error_type=type(error).__name__)
            stopped.wait(1)
