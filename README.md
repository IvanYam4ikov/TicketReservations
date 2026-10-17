# Front Row — Ticket Reservations, Milestone 4

A PostgreSQL + FastAPI ticketing project with a concert-style web interface, five-minute seat holds, simulated purchases, user accounts, and private ticket history. It demonstrates relational constraints, row locking, atomic multi-seat operations, idempotency, ownership checks, and keyset pagination.

## Run

Start Docker Desktop, then:

```sh
docker compose up --build -d api
```

Open **http://localhost:8000**. API documentation is at http://localhost:8000/docs.

Startup applies SQL migrations and seeds one event with 20 seats, without resetting existing reservations or purchases. PostgreSQL is exposed only on localhost:5433, with database/user/password `ticketing` (local demo credentials).

```sh
docker compose down
```

Stopping services retains the database volume.

## Try it

1. Click **Sign in / Register**, then **New here? Create an account**. Use an email-shaped identifier and a password of 12–128 characters. This demo sends no email and offers no password reset.
2. Select an event and available seats, then click **Hold selected seats**.
3. Complete checkout within five minutes using **Approve payment**. A receipt shows your order and ticket IDs; seats become permanently sold.
4. Open **My Tickets** to view purchased tickets. The **Reservations** tab includes holds and lets you resume checkout. Use **Load more** for older records.
5. Sign out, then sign back in. Your history is stored in PostgreSQL and survives browser restarts.
6. Use a separate browser profile or private window for a second account. Tabs in one browser profile share a login cookie.

**Decline payment** returns an error and leaves the hold intact until its original deadline. A subsequent attempt uses a new key. An uncertain network response exposes **Retry same payment**, which reuses the same key and outcome, including after expiration.

The seat map refreshes every five seconds while the tab is visible. Keyboard users can tab to seats and toggle them with Enter or Space. The countdown uses the browser clock; PostgreSQL is the expiration authority. Holds cannot yet be cancelled. If a reservation response is lost, use My Tickets → Reservations to recover the hold.

Only bookings created after accounts were introduced have owners. Older anonymous reservations, orders, tickets, and checkout attempts remain stored and unclaimed. No new account can read, purchase, or claim them. Previously sold seats remain sold.

The browser stores the active booking and pending checkout key under a user-specific session-storage key. Passwords and authentication tokens are not stored there. Logout clears private content from the page; signing in as the same user can recover a pending attempt. Account changes in another tab are detected on the next poll, and protected requests include the expected account ID to reject a cookie/account mismatch. A request already accepted by the server can finish after logout.

## API walkthrough

Events and inventory are public. Reservations, checkout, order details, and history require the session cookie. All mutating requests, including login and logout, require `X-Requested-With: FrontRow`. In Swagger, enter `FrontRow` in that header field and register/login before booking; the browser retains the cookie.

With curl, create a local demo account and keep its cookie jar private:

```sh
curl -c /tmp/front-row.cookies http://localhost:8000/auth/register \
  -H 'X-Requested-With: FrontRow' \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"local-demo-password-2026"}'
```

For subsequent visits use `/auth/login` with the same body. The example password is for a local disposable demo only.

```sh
curl http://localhost:8000/events
curl http://localhost:8000/events/11111111-1111-4111-8111-111111111111/seats
curl -b /tmp/front-row.cookies http://localhost:8000/reservations \
  -H 'X-Requested-With: FrontRow' \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"11111111-1111-4111-8111-111111111111","seat_numbers":[1,2]}'
```

Adjust seat numbers if unavailable. Substitute the returned reservation ID:

```sh
curl -b /tmp/front-row.cookies http://localhost:8000/reservations/RESERVATION_ID/checkout \
  -H 'X-Requested-With: FrontRow' \
  -H 'Idempotency-Key: 22222222-2222-4222-8222-222222222222' \
  -H 'Content-Type: application/json' \
  -d '{"payment_outcome":"succeeded"}'

curl -b /tmp/front-row.cookies 'http://localhost:8000/me/orders?limit=10'
curl -b /tmp/front-row.cookies 'http://localhost:8000/me/reservations?limit=10'
curl -b /tmp/front-row.cookies -X POST http://localhost:8000/auth/logout \
  -H 'X-Requested-With: FrontRow'
```

Checkout returns **200** with the order and tickets; its identical retry returns the same result. A simulated decline returns **402** and is persisted for replay. A changed payload using the same account/key returns **409**. A new attempt after a decline needs a new key. Another key for an already-purchased reservation returns **409**. Keys are unique per user, so two users can independently use the same UUID. Validation, expired-hold errors, and failed transactions do not consume keys.

Anonymous private requests return **401**. Missing resources, other users' resources, and unclaimed legacy resources return **404**. This includes checkout retries: ownership is checked before idempotency replay. `GET /auth/me` returns the current account; `GET /orders/{id}` and `GET /reservations/{id}` return private details.

History returns `{ "items": [...], "next_cursor": "..." }`. Send `next_cursor` back as the `cursor` query parameter to fetch older records. Limits are 1–50, default 10. Ordering is `(created_at DESC, id DESC)`, backed by indexes beginning with `user_id`. This avoids offset drift when new purchases arrive. A cursor is a position, not authorization; every page still filters by the authenticated user. Orders retain their original currency and price snapshots.

## Authentication and security design

- Passwords use independently salted scrypt hashes (`N=32768, r=8, p=3`, 32-byte output). This is one of the [OWASP recommended scrypt configurations](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
- Login creates a random 256-bit opaque session token. Only its SHA-256 digest is stored in PostgreSQL. Sessions expire after seven days; logout revokes the current session, and a new login rotates the current browser session.
- Cookies are HttpOnly, SameSite=Strict, host-only, and scoped to `/`. HTTPS automatically adds Secure; set `COOKIE_SECURE=true` when HTTPS terminates at a correctly configured proxy. Plain HTTP cookies support localhost development only.
- Mutating requests require a custom header, reject mismatched Origin and cross-site Fetch Metadata, and do not enable CORS. This follows the [OWASP custom-header CSRF pattern](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#employing-custom-request-headers-for-ajaxapi). Do not enable permissive credentialed CORS.
- Auth/private responses use `Cache-Control: no-store`. Users are resolved from a valid session, never from request-body ownership fields.
- Database-backed limits allow ten login attempts per normalized email and thirty registrations per client address per fifteen minutes. These basic demo limits can lock an account temporarily and are not a complete public-service abuse defense. Proxy address handling and perimeter rate limits need deployment-specific configuration.
- Ownership is enforced by SQL predicates and composite foreign keys. This milestone does not implement PostgreSQL row-level security. Direct database access is trusted.

No real payment provider is connected. The client chooses a simulated outcome; no money is charged. Real payments need verified provider responses, provider-side idempotency, and reconciliation/refunds when payment arrives after hold expiration. Network calls must stay outside inventory transactions.

## Data model and transactions

```mermaid
erDiagram
    users ||--o{ sessions : authenticates
    users o|--o{ reservations : owns
    users o|--o{ orders : owns
    events ||--o{ event_seats : inventories
    events ||--o{ reservations : receives
    reservations o|--o{ event_seats : currently_holds
    reservations ||--|{ reservation_items : records
    reservations ||--o| orders : purchases
    orders ||--|{ tickets : issues
    event_seats ||--o| tickets : sells
    users o|--o{ checkout_attempts : scopes
```

A hold locks all requested `event_seats` in ascending seat order using `FOR UPDATE`, checks existence and availability after lock acquisition, creates the reservation and item price snapshots, and updates inventory pointers in one transaction. One unavailable seat rolls back the entire group. Expired holds are reclaimable without a cleanup worker.

Checkout checks ownership, takes an advisory transaction lock scoped to the user/idempotency key, then locks inventory in the same seat order. After waiting, it checks ownership of all seats, prior purchase, and current database time. Order, tickets, and successful attempt commit together. Validity is decided at this post-lock check; a valid purchase can commit after the deadline while retaining its locks.

Unique constraints prohibit duplicate event-seat inventory, multiple orders for one reservation, and multiple tickets for an event seat. Composite foreign keys bind owned orders and attempts to the correct reservation owner. Nullable ownership supports historical records only; new API writes always supply the authenticated owner. Structural constraints complement the locking protocol; arbitrary SQL writers must not bypass it.

Migration `003_accounts.sql` is additive except for replacing the old global idempotency primary key with a surrogate ID and a user/key uniqueness constraint. A partial unique index preserves legacy key uniqueness. It does not rewrite or assign legacy bookings. Startup migrations are transactional and serialized by an advisory lock.

## Tests

```sh
docker compose --profile test run --build --rm test
```

The suite creates a unique PostgreSQL schema per test and removes only that schema afterward; demo bookings are not cleared. PostgreSQL allows 200 connections for the 100-client race. Tests cover:

- 100 simultaneous seat requests: one reservation, 99 conflicts.
- Atomic overlapping groups, expiration/reassignment races, and post-lock expiration checks.
- Concurrent identical/different checkout keys, decline replay, and injected ticket-issuance failure rollback.
- Registration, hashing, session rotation/revocation/expiration, CSRF, and login throttling.
- Two users' isolation, including idempotency replay and independent use of the same key.
- Private history pagination, tied timestamps, new inserts, and snapshot currency.
- Upgrade of a pre-account database with an existing purchase and preservation of unclaimed records.

Optional frontend state tests require Node.js 18+:

```sh
node --test tests/frontend.test.cjs
```

These use a small DOM stub to test booking, retries, sign-in requirements, account switching, logout clearing, and history state. They do not replace visual or browser end-to-end testing. The frontend itself requires no Node runtime/build step; FastAPI serves packaged HTML/CSS/JavaScript. Typography optionally uses Google Fonts with local fallbacks.

## Local Python development

Python 3.12+ is required (Docker uses 3.13):

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -c requirements.lock -e '.[test]'
docker compose up -d db
python -m ticketing.manage migrate
python -m ticketing.manage seed
uvicorn ticketing.main:app --reload
```

Stop the containerized API first if it occupies port 8000. `DATABASE_URL` defaults to the local demo instance. Run Python tests locally with:

```sh
TEST_DATABASE_URL=postgresql://ticketing:ticketing@localhost:5433/ticketing pytest -q
```

The database role must be able to create schemas. Missing `TEST_DATABASE_URL` fails rather than silently skipping integration tests. `requirements.lock` constrains dependency versions in container builds.

## Limits and next work

This remains a local portfolio demo. Email verification, password reset, MFA, account deletion, cancellation/refunds, real payments, session-management UI, and production deployment hardening are not implemented. Expired sessions, rate-limit keys, and checkout attempts have no scheduled cleanup yet. Next database-focused work: load testing, `EXPLAIN (ANALYZE, BUFFERS)` studies, connection pooling, and measured backup/restore exercises.

## Code map

- `ticketing/main.py`: public inventory and owned reservations.
- `ticketing/auth.py`: passwords, sessions, CSRF, and account endpoints.
- `ticketing/history.py`: private history with keyset pagination.
- `ticketing/checkout.py`: owned checkout and replay semantics.
- `ticketing/migrations/`: versioned schema and constraints.
- `ticketing/manage.py`: migration runner and repeatable seed.
- `ticketing/static/`: web interface and API client.
- `tests/`: PostgreSQL integration and frontend state tests.
