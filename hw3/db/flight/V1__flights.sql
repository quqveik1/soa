CREATE TABLE airlines (
    code VARCHAR(2) PRIMARY KEY CHECK (code ~ '^[A-Z0-9]{2}$'),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0)
);

CREATE TABLE flights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    airline_code VARCHAR(2) NOT NULL REFERENCES airlines(code),
    flight_number VARCHAR(8) NOT NULL CHECK (flight_number ~ '^[A-Z0-9]{2}[0-9]{1,6}$'),
    origin VARCHAR(3) NOT NULL CHECK (origin ~ '^[A-Z]{3}$'),
    destination VARCHAR(3) NOT NULL CHECK (destination ~ '^[A-Z]{3}$'),
    departure_at TIMESTAMPTZ NOT NULL,
    arrival_at TIMESTAMPTZ NOT NULL CHECK (arrival_at > departure_at),
    total_seats INTEGER NOT NULL CHECK (total_seats BETWEEN 1 AND 1000),
    available_seats INTEGER NOT NULL CHECK (available_seats >= 0 AND available_seats <= total_seats),
    price NUMERIC(12,2) NOT NULL CHECK (price > 0),
    status TEXT NOT NULL CHECK (status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED')),
    CHECK (origin <> destination)
);

CREATE UNIQUE INDEX flights_number_departure_date ON flights
    (flight_number, ((departure_at AT TIME ZONE 'UTC')::date));
CREATE INDEX flights_search ON flights (origin, destination, departure_at)
    WHERE status = 'SCHEDULED';

CREATE TABLE seat_reservations (
    booking_id UUID PRIMARY KEY,
    flight_id UUID NOT NULL REFERENCES flights(id),
    seat_count INTEGER NOT NULL CHECK (seat_count BETWEEN 1 AND 1000),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX seat_reservations_flight ON seat_reservations(flight_id);

-- A release can arrive before an earlier reserve finishes over the network.
CREATE TABLE reservation_cancellations (
    booking_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cache_versions (
    scope TEXT PRIMARY KEY,
    version BIGINT NOT NULL CHECK (version > 0)
);
