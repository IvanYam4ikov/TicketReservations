CREATE TABLE users (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE CHECK (email = lower(email)),
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE sessions (
    token_hash text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX sessions_user_idx ON sessions(user_id);
CREATE INDEX sessions_expiry_idx ON sessions(expires_at);
CREATE TABLE auth_buckets (
    key text PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts integer NOT NULL
);
-- NULL preserves pre-account demo records. No account can claim them via the API.
ALTER TABLE reservations ADD COLUMN user_id uuid REFERENCES users(id);
ALTER TABLE reservations ADD CONSTRAINT reservations_id_user_key UNIQUE (id, user_id);
CREATE INDEX reservations_user_history_idx ON reservations(user_id, created_at DESC, id DESC);
ALTER TABLE orders ADD COLUMN user_id uuid REFERENCES users(id);
ALTER TABLE orders ADD CONSTRAINT orders_reservation_owner_fk
    FOREIGN KEY (reservation_id, user_id) REFERENCES reservations(id, user_id);
ALTER TABLE orders ADD CONSTRAINT orders_id_user_key UNIQUE (id, user_id);
CREATE INDEX orders_user_history_idx ON orders(user_id, created_at DESC, id DESC);
ALTER TABLE checkout_attempts ADD COLUMN user_id uuid REFERENCES users(id);
ALTER TABLE checkout_attempts ADD CONSTRAINT attempts_reservation_owner_fk
    FOREIGN KEY (reservation_id, user_id) REFERENCES reservations(id, user_id);
ALTER TABLE checkout_attempts ADD CONSTRAINT attempts_order_owner_fk
    FOREIGN KEY (order_id, user_id) REFERENCES orders(id, user_id);
ALTER TABLE checkout_attempts DROP CONSTRAINT checkout_attempts_pkey;
ALTER TABLE checkout_attempts ADD COLUMN id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY;
ALTER TABLE checkout_attempts ADD CONSTRAINT attempts_user_key UNIQUE (user_id, idempotency_key);
CREATE UNIQUE INDEX attempts_legacy_key ON checkout_attempts(idempotency_key) WHERE user_id IS NULL;
