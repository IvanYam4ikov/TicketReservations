# Ticket Reservations — Milestone 3

A runnable PostgreSQL + FastAPI project demonstrating atomic, concurrency-safe seat holds. Includes one seeded event, 20 seats, five-minute reservations, idempotent simulated checkout, issued tickets, SQL migrations, and real-database integration tests.

## Run

Install and start Docker Desktop, then from this directory:

```sh
docker compose up --build -d api
```

Open http://localhost:8000 for the Front Row web interface. Interactive API documentation remains at http://localhost:8000/docs. Startup applies migrations and seeds demo data without overwriting reservations. PostgreSQL is available on localhost:5433 with database/user/password `ticketing` (local demo credentials).

```sh
curl http://localhost:8000/events
curl http://localhost:8000/events/11111111-1111-4111-8111-111111111111/seats
curl -i http://localhost:8000/reservations \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"11111111-1111-4111-8111-111111111111","seat_numbers":[1,2]}'
```

The first reservation returns **201** with its ID, expiry, seats, and total price in cents. Repeating it within five minutes returns **409**. Read a reservation with `GET /reservations/{id}`. Unpurchased seats are available again after expiry, without a cleanup worker. Purchased seats remain sold. Inventory is a snapshot; reservation is the authoritative availability check.

## Try the web interface

1. Choose an event and select up to ten available seats.
2. Click **Hold selected seats** to start a five-minute hold.
3. Choose **Approve payment** or **Decline payment**, then complete the simulated purchase.
4. A successful purchase displays the order and ticket IDs and marks the seats sold.

The seat map refreshes every five seconds while the tab is visible. Keyboard users can tab to seats and toggle them with Enter or Space. The layout adapts to narrow screens. After a decline, select Approve payment to start a new attempt. After an uncertain network result, **Retry same payment** reuses the original key and outcome, including after the hold deadline.

The active hold, pending checkout key, and latest receipt are stored in this tab's session storage so a reload can recover them. This is not account history and is lost when the browser session is cleared. A hold cannot yet be cancelled; wait for its deadline before choosing another group. The countdown uses the browser clock; PostgreSQL remains the authority for expiration. If a reservation request loses its response, its hold cannot be recovered by this UI and will expire naturally. Payments are simulated and no card data is collected.

The UI is served by FastAPI from packaged HTML, CSS, and JavaScript, with no frontend build or Node installation required. Typography optionally loads from Google Fonts; system fonts work offline.

## Checkout demo

Create a reservation using the example above, then substitute its ID below. Supply a UUID in the `Idempotency-Key` header; reuse that same key and body when retrying a request after a timeout.

```sh
curl -i http://localhost:8000/reservations/RESERVATION_ID/checkout \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 22222222-2222-4222-8222-222222222222' \
  -d '{"payment_outcome":"succeeded"}'
```

Checkout returns **200** with the order, currency, total price, and tickets. `GET /orders/{order_id}` retrieves the purchase. The reservation becomes `confirmed` and inventory becomes `sold`. Both the first success and its retries return the same status and JSON, even after the original hold deadline.

Use `"payment_outcome":"declined"` to simulate a decline (**402**). A decline preserves the hold until its original expiration and is saved for replay. To try payment again, use a new key. A key reused with another reservation or outcome returns **409**. Another key for an already-purchased reservation also returns **409**; use the original successful key to retrieve its response. Validation, missing-reservation, and expired-hold errors do not consume keys. A failed transaction leaves no attempt, order, or tickets, so the same key can be retried.

This is a local simulator: the client chooses the outcome and no money is charged. Real provider integration requires verified provider responses, provider-side idempotency, and reconciliation/refunds for payments that arrive after a hold expires. External payment calls must happen outside inventory transactions.

Stop services while keeping data:

```sh
docker compose down
```

## Test

```sh
docker compose --profile test run --build --rm test
```

The suite uses real PostgreSQL and creates a uniquely named schema per test, removing only that schema afterward. It never clears demo inventory. PostgreSQL is configured for 200 connections to accommodate the 100-client race.

Tests cover:

- 100 clients released together to reserve one seat: one 201, 99 conflicts, one persisted reservation.
- Overlapping seat groups: one complete group wins and the loser leaves no partial allocation.
- Expired hold reclamation under contention, with historical items preserved.
- Missing-seat rollback, invalid input, unknown resources, repeatable migrations and seeds.
- Concurrent checkouts using identical and different keys: one persisted order and ticket set.
- Decline replay, request/key mismatch, price snapshots, and sold seats after hold expiry.
- Checkout blocked on an inventory lock until after expiry, plus checkout racing seat reassignment.
- Injected failure during ticket issuance: the entire order, ticket set, and attempt roll back.
- Database rejection of a duplicate ticket for an event seat.

These are correctness tests, not a throughput benchmark; client scheduling does not guarantee all 100 transactions hold database connections at the same instant.

## Local Python development

Python 3.12+ is required; the container uses 3.13.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -c requirements.lock -e '.[test]'
docker compose up -d db
python -m ticketing.manage migrate
python -m ticketing.manage seed
uvicorn ticketing.main:app --reload
```

Stop the containerized API first if it already occupies port 8000. `DATABASE_URL` defaults to the local demo PostgreSQL instance. To test locally:

```sh
TEST_DATABASE_URL=postgresql://ticketing:ticketing@localhost:5433/ticketing pytest -q
```

The database role must be allowed to create schemas. Missing `TEST_DATABASE_URL` fails explicitly rather than silently skipping integration tests. `requirements.lock` records resolved dependencies and constrains container builds.

## Schema

```mermaid
erDiagram
    events ||--o{ event_seats : inventories
    events ||--o{ reservations : receives
    reservations o|--o{ event_seats : currently_holds
    reservations ||--|{ reservation_items : records
    event_seats ||--o{ reservation_items : history
    reservations ||--o| orders : purchases
    orders ||--|{ tickets : issues
    event_seats ||--o| tickets : sells
    reservations ||--o{ checkout_attempts : attempts
```

- `events`: event identity, start time, ISO-style three-letter currency code.
- `event_seats`: one row per event/seat; nonnegative integer price and current reservation pointer.
- `reservations`: creation/expiration times from the database clock.
- `reservation_items`: historical allocation and price snapshot, preserved after expiration/reassignment.

Composite foreign keys ensure a seat's reservation belongs to the same event. Primary keys prohibit duplicate event-seat inventory and duplicate seats within a reservation. Milestone 1 uses numbered seats directly; reusable venues and physical layouts come later.

## Transaction design

1. Validate a request for 1–10 distinct positive seat numbers.
2. Begin a PostgreSQL transaction (default READ COMMITTED).
3. Lock every requested inventory row using `ORDER BY seat_number FOR UPDATE`.
4. Fail if any seat is missing. Read database time and current reservation expiration **after acquiring locks**.
5. If any existing hold is active, roll back with HTTP 409.
6. Insert a five-minute reservation, update all inventory pointers, snapshot prices, and commit before returning HTTP 201.

The separate post-lock statement gets a fresh READ COMMITTED snapshot, including reservations committed while this request waited for locks. Locking rows in a consistent order avoids cycles among these booking operations. Every future allocation/checkout/cancellation path must follow the same locking protocol.

The database transaction makes multi-seat allocation atomic; row locks serialize competing writers. Constraints reinforce structural integrity, but cannot by themselves enforce this time-dependent allocation policy against arbitrary SQL writers. Only trusted application code should write inventory.

Expiration is derived from timestamps, so a delayed worker cannot keep a seat unavailable. Historical reservations remain expired even after a later reservation takes ownership of their former seats. No locks are held while calling external services.

## Checkout transaction and constraints

Migration `002_checkout.sql` adds tables without changing existing inventory or reservations. Startup applies it to the existing database; no volume reset is needed.

Checkout takes a transaction-scoped advisory lock for the idempotency key, then locks inventory rows in the same ascending seat order as reservations. After the locks are acquired, it checks for an existing purchase, reads current database time, and verifies ownership of every seat. Validity is decided at that check; an accepted checkout can commit after the deadline while retaining its locks. Competing requests wait and observe the committed result.

Order, tickets, and successful attempt commit together. A unique reservation on `orders` prevents duplicate purchases; a unique `(event_id, seat_number)` on `tickets` prevents double issuance. Composite foreign keys tie tickets to the correct order, event, reservation, and original reserved seat. Prices come from reservation snapshots, not current inventory pricing. Hold creation checks for tickets after acquiring inventory locks, so sold seats cannot be reclaimed after expiration.

Idempotency keys are globally scoped UUIDs for this anonymous demo and attempts are retained indefinitely. A production authenticated service should scope keys to a customer and define a retention policy. The simulator executes inside the transaction because it makes no network call.

## Scope and next milestone

This milestone is a local, anonymous reservation and simulated-purchase API. It does not yet include authentication/ownership authorization, real payments, rate limiting, connection pooling, cancellation/refunds, or customer ticket history. The web interface supports the anonymous demo flow. Keep it local until access controls are implemented.

Next: authentication, customer ownership checks, and personal ticket history. Later work can add query-plan benchmarks, load testing, backup/restore exercises, monitoring, and CI.

## Code map

- `ticketing/main.py`: HTTP endpoints and reservation transaction.
- `ticketing/migrations/001_initial.sql`: database schema and constraints.
- `ticketing/manage.py`: transactional migration runner and repeatable seed.
- `tests/test_reservations.py`: correctness and concurrency evidence.

- `ticketing/checkout.py`: checkout, replay semantics, and order retrieval.
- `ticketing/migrations/002_checkout.sql`: order, ticket, and attempt constraints.
- `tests/test_checkout.py`: purchase correctness, failure injection, and concurrency evidence.

- `ticketing/static/`: responsive web interface and API client.

Frontend state tests (optional Node.js 18+):

```sh
node --test tests/frontend.test.cjs
```

These exercise selection, checkout success, decline retries, uncertain-response idempotency, and expiration using a small DOM stub. They do not replace visual or end-to-end browser testing.
