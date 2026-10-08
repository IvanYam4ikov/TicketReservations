CREATE TABLE events (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    starts_at timestamptz NOT NULL,
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$')
);
CREATE TABLE reservations (
    id uuid PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES events(id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    UNIQUE (id, event_id),
    CHECK (expires_at > created_at)
);
CREATE TABLE event_seats (
    event_id uuid NOT NULL REFERENCES events(id),
    seat_number integer NOT NULL CHECK (seat_number > 0),
    price_cents integer NOT NULL CHECK (price_cents >= 0),
    reservation_id uuid,
    PRIMARY KEY (event_id, seat_number),
    FOREIGN KEY (reservation_id, event_id) REFERENCES reservations(id, event_id)
);
CREATE TABLE reservation_items (
    reservation_id uuid NOT NULL,
    event_id uuid NOT NULL,
    seat_number integer NOT NULL,
    price_cents integer NOT NULL CHECK (price_cents >= 0),
    PRIMARY KEY (reservation_id, seat_number),
    FOREIGN KEY (reservation_id, event_id) REFERENCES reservations(id, event_id),
    FOREIGN KEY (event_id, seat_number) REFERENCES event_seats(event_id, seat_number)
);
CREATE INDEX reservation_items_event_seat_idx ON reservation_items(event_id, seat_number);
CREATE INDEX event_seats_reservation_idx ON event_seats(reservation_id, event_id);
