# Ticket Reservations — Milestone 1

A runnable PostgreSQL + FastAPI project demonstrating atomic, concurrency-safe seat holds. Includes one seeded event, 20 seats, five-minute reservations, SQL migrations, and real-database integration tests.

## Run

Install and start Docker Desktop, then from this directory:

```sh
docker compose up --build -d api
```

Open http://localhost:8000/docs for interactive API documentation. Startup applies migrations and seeds demo data without overwriting reservations. PostgreSQL is available on localhost:5433 with database/user/password `ticketing` (local demo credentials).

```sh
curl http://localhost:8000/events
curl http://localhost:8000/events/11111111-1111-4111-8111-111111111111/seats
curl -i http://localhost:8000/reservations \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"11111111-1111-4111-8111-111111111111","seat_numbers":[1,2]}'
```

The first reservation returns **201** with its ID, expiry, seats, and total price in cents. Repeating it within five minutes returns **409**. Read a reservation with `GET /reservations/{id}`. Seats are available again after expiry, without a cleanup worker. Inventory is a snapshot; reservation is the authoritative availability check.

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

## Scope and next milestone

This milestone is a local, anonymous hold API. It does not yet include authentication/ownership authorization, purchases, payment processing, idempotency keys, rate limiting, connection pooling, cancellation, or a custom frontend. The interactive API documentation provides the initial demo interface. Keep it local until access controls are implemented.

Next: add order/ticket constraints and idempotent checkout, with tests racing purchase against hold expiration and reassignment. Later work can add query-plan benchmarks, load testing, backup/restore exercises, monitoring, and CI.

## Code map

- `ticketing/main.py`: HTTP endpoints and reservation transaction.
- `ticketing/migrations/001_initial.sql`: database schema and constraints.
- `ticketing/manage.py`: transactional migration runner and repeatable seed.
- `tests/test_reservations.py`: correctness and concurrency evidence.
