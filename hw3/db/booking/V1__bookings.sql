CREATE TABLE bookings (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    flight_id UUID NOT NULL,
    passenger_name TEXT NOT NULL CHECK (length(trim(passenger_name)) BETWEEN 1 AND 200),
    passenger_email VARCHAR(254) NOT NULL,
    seat_count INTEGER NOT NULL CHECK (seat_count BETWEEN 1 AND 1000),
    total_price NUMERIC(15,2) NOT NULL CHECK (total_price > 0),
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'CANCELLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX bookings_user ON bookings(user_id, created_at DESC, id);

-- Intents survive a crash between the two independent database commits.
CREATE TABLE pending_operations (
    booking_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('CREATE', 'CANCEL')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (booking_id, kind)
);
CREATE INDEX pending_operations_created ON pending_operations(created_at);
