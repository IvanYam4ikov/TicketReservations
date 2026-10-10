CREATE TABLE orders (
    id uuid PRIMARY KEY,
    reservation_id uuid NOT NULL UNIQUE,
    event_id uuid NOT NULL,
    total_price_cents bigint NOT NULL CHECK (total_price_cents >= 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (id, reservation_id, event_id),
    FOREIGN KEY (reservation_id, event_id) REFERENCES reservations(id, event_id)
);
CREATE TABLE tickets (
    id uuid PRIMARY KEY,
    order_id uuid NOT NULL,
    reservation_id uuid NOT NULL,
    event_id uuid NOT NULL,
    seat_number integer NOT NULL,
    price_cents integer NOT NULL CHECK (price_cents >= 0),
    UNIQUE (event_id, seat_number),
    FOREIGN KEY (order_id, reservation_id, event_id) REFERENCES orders(id, reservation_id, event_id),
    FOREIGN KEY (reservation_id, seat_number) REFERENCES reservation_items(reservation_id, seat_number)
);
CREATE INDEX tickets_order_idx ON tickets(order_id);
CREATE TABLE checkout_attempts (
    idempotency_key uuid PRIMARY KEY,
    reservation_id uuid NOT NULL REFERENCES reservations(id),
    payment_outcome text NOT NULL CHECK (payment_outcome IN ('succeeded', 'declined')),
    order_id uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((payment_outcome = 'succeeded') = (order_id IS NOT NULL)),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
