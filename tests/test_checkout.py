from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg.conninfo import make_conninfo
from ticketing.db import connect
from ticketing.main import create_app
from ticketing.manage import DEMO_EVENT_ID, migrate


def hold(client, seats=(1, 2)):
    response = client.post('/reservations', json={'event_id': DEMO_EVENT_ID, 'seat_numbers': list(seats)})
    assert response.status_code == 201
    return response.json()['id']


def buy(client, reservation, key=None, outcome='succeeded'):
    return client.post(f'/reservations/{reservation}/checkout',
                       headers={'Idempotency-Key': str(key or uuid4())},
                       json={'payment_outcome': outcome})


def expire(database_url, reservation):
    with connect(database_url) as conn:
        conn.execute("""UPDATE reservations SET created_at=clock_timestamp()-interval '10 minutes',
                     expires_at=clock_timestamp()-interval '5 minutes' WHERE id=%s""", (reservation,))


def test_purchase_snapshots_prices_and_remains_sold(client, database_url):
    reservation = hold(client)
    with connect(database_url) as conn:
        conn.execute('UPDATE event_seats SET price_cents=9999')
    key = uuid4()
    response = buy(client, reservation, key)
    assert response.status_code == 200
    order = response.json()
    assert order['total_price_cents'] == 15000
    assert order['currency'] == 'USD'
    assert [t['seat_number'] for t in order['tickets']] == [1, 2]
    assert all(t['price_cents'] == 7500 for t in order['tickets'])
    assert client.get(f"/orders/{order['id']}").json() == order
    expire(database_url, reservation)
    assert buy(client, reservation, key).json() == order
    assert client.get(f'/reservations/{reservation}').json()['status'] == 'confirmed'
    assert client.get(f'/events/{DEMO_EVENT_ID}/seats').json()[0]['status'] == 'sold'
    assert client.post('/reservations', json={'event_id': DEMO_EVENT_ID, 'seat_numbers': [1]}).status_code == 409
    migrate(database_url)
    assert client.get(f"/orders/{order['id']}").json() == order


@pytest.mark.parametrize('same_key', [True, False])
def test_concurrent_checkout_once(client, database_url, same_key):
    reservation = hold(client)
    shared_key = uuid4()
    barrier = Barrier(20)

    def attempt(_):
        with TestClient(create_app(database_url)) as c:
            barrier.wait(timeout=15)
            return buy(c, reservation, shared_key if same_key else uuid4())

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))
    successes = [r.json() for r in results if r.status_code == 200]
    assert len(successes) == (20 if same_key else 1)
    assert all(r.status_code in (200, 409) for r in results)
    assert all(o == successes[0] for o in successes)
    with connect(database_url) as conn:
        assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM tickets').fetchone()['n'] == 2
        assert conn.execute('SELECT count(*) AS n FROM checkout_attempts').fetchone()['n'] == 1


def test_decline_replay_and_new_attempt(client, database_url):
    reservation = hold(client)
    key = uuid4()
    assert buy(client, reservation, key, 'declined').status_code == 402
    assert buy(client, reservation, key, 'declined').status_code == 402
    assert buy(client, reservation, key, 'succeeded').status_code == 409
    with connect(database_url) as conn:
        assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM tickets').fetchone()['n'] == 0
    assert client.get(f'/reservations/{reservation}').json()['status'] == 'held'
    assert buy(client, reservation).status_code == 200
    # A retry of the old declined attempt remains declined even after a later success.
    assert buy(client, reservation, key, 'declined').status_code == 402


def test_key_cannot_be_reused_for_another_reservation(client):
    first, second = hold(client, [1]), hold(client, [2])
    key = uuid4()
    assert buy(client, first, key).status_code == 200
    assert buy(client, second, key).status_code == 409
    assert buy(client, second).status_code == 200


def test_expired_reassigned_hold_cannot_purchase(client, database_url):
    old = hold(client)
    expire(database_url, old)
    new = hold(client)
    assert buy(client, old).status_code == 409
    assert buy(client, new).status_code == 200


def test_checkout_rechecks_time_after_waiting_for_seat_lock(client, database_url):
    reservation = hold(client, [1])
    # Give the hold a short lifetime, then deliberately block checkout on inventory.
    with connect(database_url) as conn:
        conn.execute("UPDATE reservations SET expires_at=clock_timestamp()+interval '1 second' WHERE id=%s",
                     (reservation,))
    name = 'checkout_race_' + uuid4().hex
    race_url = make_conninfo(database_url, application_name=name)
    with connect(database_url) as locker, ThreadPoolExecutor(max_workers=1) as pool:
        locker.execute('SELECT * FROM event_seats WHERE seat_number=1 FOR UPDATE')

        def checkout():
            with TestClient(create_app(race_url)) as c:
                return buy(c, reservation)

        future = pool.submit(checkout)
        try:
            deadline = time.monotonic() + 10
            blocked = False
            while time.monotonic() < deadline:
                with connect(database_url) as observer:
                    blocked = observer.execute(
                        "SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND cardinality(pg_blocking_pids(pid))>0",
                        (name,),
                    ).fetchone()
                    expired = observer.execute('SELECT expires_at<=clock_timestamp() AS expired FROM reservations WHERE id=%s',
                                               (reservation,)).fetchone()['expired']
                if blocked and expired:
                    break
                time.sleep(0.02)
            assert blocked and expired, 'Checkout did not wait on the seat lock across expiry'
        finally:
            locker.commit()  # Always unblock the worker, even if the assertion fails.
        assert future.result(timeout=10).status_code == 409
    new = hold(client, [1])
    assert buy(client, new).status_code == 200


def test_failure_during_ticket_issue_rolls_back_entire_checkout(client, database_url):
    reservation = hold(client)
    with connect(database_url) as conn:
        conn.execute("""CREATE FUNCTION reject_second_ticket() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.seat_number=2 THEN RAISE EXCEPTION 'Injected ticket failure'; END IF;
        RETURN NEW; END $$;
        CREATE TRIGGER reject_ticket BEFORE INSERT ON tickets
        FOR EACH ROW EXECUTE FUNCTION reject_second_ticket();""")
    key = uuid4()
    with TestClient(create_app(database_url), raise_server_exceptions=False) as c:
        assert buy(c, reservation, key).status_code == 500
    with connect(database_url) as conn:
        for table in ('orders', 'tickets', 'checkout_attempts'):
            assert conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] == 0
        conn.execute('DROP TRIGGER reject_ticket ON tickets')
    assert buy(client, reservation, key).status_code == 200


def test_database_rejects_duplicate_ticket(client, database_url):
    order = buy(client, hold(client)).json()
    with pytest.raises(errors.UniqueViolation), connect(database_url) as conn:
        conn.execute('''INSERT INTO tickets (id, order_id, reservation_id, event_id, seat_number, price_cents)
                     VALUES (%s,%s,%s,%s,1,7500)''',
                     (uuid4(), order['id'], order['reservation_id'], DEMO_EVENT_ID))


def test_checkout_validation(client):
    reservation = hold(client)
    assert client.post(f'/reservations/{reservation}/checkout', json={}).status_code == 422
    assert buy(client, reservation, 'invalid').status_code == 422
    assert buy(client, reservation, outcome='unknown').status_code == 422
    assert buy(client, uuid4()).status_code == 404
    assert client.get(f'/orders/{uuid4()}').status_code == 404


def test_expired_checkout_races_with_reassignment(client, database_url):
    old = hold(client, [1])
    expire(database_url, old)
    barrier = Barrier(2)

    def checkout_old():
        with TestClient(create_app(database_url)) as c:
            barrier.wait(timeout=10)
            return buy(c, old)

    def reassign():
        with TestClient(create_app(database_url)) as c:
            barrier.wait(timeout=10)
            return hold(c, [1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        old_future = pool.submit(checkout_old)
        new_future = pool.submit(reassign)
        assert old_future.result(timeout=15).status_code == 409
        new = new_future.result(timeout=15)
    order = buy(client, new)
    assert order.status_code == 200
    with connect(database_url) as conn:
        tickets = conn.execute('SELECT reservation_id FROM tickets').fetchall()
        assert [str(t['reservation_id']) for t in tickets] == [new]
