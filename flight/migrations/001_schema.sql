CREATE TABLE airlines (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE CHECK (code ~ '^[A-Z0-9]{2}$'),
    name text NOT NULL CHECK (length(trim(name)) > 0)
);

CREATE TABLE flight_numbers (
    flight_number text PRIMARY KEY CHECK (flight_number ~ '^[A-Z0-9]{2}[0-9]{1,4}$'),
    airline_id integer NOT NULL REFERENCES airlines(id)
);

CREATE TABLE flights (
    id uuid PRIMARY KEY,
    flight_number text NOT NULL REFERENCES flight_numbers(flight_number),
    origin text NOT NULL CHECK (origin ~ '^[A-Z]{3}$'),
    destination text NOT NULL CHECK (destination ~ '^[A-Z]{3}$'),
    departure_time timestamptz NOT NULL,
    arrival_time timestamptz NOT NULL CHECK (arrival_time > departure_time),
    total_seats integer NOT NULL CHECK (total_seats > 0),
    available_seats integer NOT NULL CHECK (available_seats BETWEEN 0 AND total_seats),
    price bigint NOT NULL CHECK (price > 0),
    status text NOT NULL CHECK (status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED')),
    CHECK (origin <> destination)
);
CREATE UNIQUE INDEX flight_number_day ON flights
    (flight_number, ((departure_time AT TIME ZONE 'UTC')::date));

CREATE TABLE seat_reservations (
    booking_id uuid PRIMARY KEY,
    flight_id uuid NOT NULL REFERENCES flights(id),
    seat_count integer NOT NULL CHECK (seat_count > 0),
    status text NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED'))
);

CREATE TABLE cache_revision (
    id integer PRIMARY KEY CHECK (id = 1),
    revision bigint NOT NULL DEFAULT 0
);
INSERT INTO cache_revision (id) VALUES (1);
