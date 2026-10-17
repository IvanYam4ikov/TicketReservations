from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import errors, sql
from psycopg.conninfo import make_conninfo
from ticketing.auth import COOKIE, token_hash
from ticketing.db import connect
from ticketing.main import create_app
from ticketing.manage import DEMO_EVENT_ID, migrate, seed
from conftest import TEST_EMAIL, TEST_PASSWORD, TEST_TOKEN
from test_checkout import hold, buy

HEADERS = {'X-Requested-With': 'FrontRow'}
PASSWORD = 'correct-horse-battery-42'


def anonymous(database_url):
    return TestClient(create_app(database_url), headers=HEADERS)


def register(client, email='second@example.com'):
    response = client.post('/auth/register', json={'email': email, 'password': PASSWORD})
    assert response.status_code == 201, response.text
    return response


def test_register_login_cookie_rotation_and_logout(database_url):
    with anonymous(database_url) as c:
        response = register(c, '  Alice@Example.com  ')
        user = response.json()
        assert user['email'] == 'alice@example.com'
        assert set(user) == {'id', 'email'}
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'SameSite=strict' in response.headers['set-cookie']
        assert response.headers['cache-control'] == 'no-store'
        token = c.cookies.get(COOKIE)
        with connect(database_url) as conn:
            record = conn.execute('SELECT password_hash FROM users WHERE id=%s', (user['id'],)).fetchone()
            assert record['password_hash'].startswith('scrypt$') and PASSWORD not in record['password_hash']
            assert conn.execute('SELECT 1 FROM sessions WHERE token_hash=%s', (token_hash(token),)).fetchone()
            assert not conn.execute('SELECT 1 FROM sessions WHERE token_hash=%s', (token,)).fetchone()
        assert c.get('/auth/me').json() == user
        assert c.post('/auth/login', json={'email': 'ALICE@example.com', 'password': PASSWORD}).status_code == 200
        assert c.cookies.get(COOKIE) != token
        with anonymous(database_url) as stale:
            stale.cookies.set(COOKIE, token)
            assert stale.get('/auth/me').status_code == 401
        new_token = c.cookies.get(COOKIE)
        assert c.post('/auth/logout').status_code == 200
        assert c.get('/auth/me').status_code == 401
        c.cookies.set(COOKIE, new_token)
        assert c.get('/auth/me').status_code == 401


def test_password_validation_duplicate_registration_and_invalid_login(database_url):
    with anonymous(database_url) as c:
        assert c.post('/auth/register', json={'email': 'no-email', 'password': PASSWORD}).status_code == 422
        assert c.post('/auth/register', json={'email': 'a@example.com', 'password': 'short'}).status_code == 422
        register(c)
        assert c.post('/auth/register', json={'email': 'SECOND@EXAMPLE.COM', 'password': PASSWORD}).status_code == 409
        for email in ['second@example.com', 'unknown@example.com']:
            response = c.post('/auth/login', json={'email': email, 'password': 'wrong-password-long'})
            assert response.status_code == 401
            assert response.json() == {'detail': 'Invalid email or password'}


def test_authentication_required_and_public_browsing(database_url):
    with anonymous(database_url) as c:
        assert c.get('/events').status_code == 200
        assert c.get(f'/events/{DEMO_EVENT_ID}/seats').status_code == 200
        assert c.post('/reservations', json={'event_id': DEMO_EVENT_ID, 'seat_numbers': [1]}).status_code == 401
        for path in ['/auth/me', '/me/orders', '/me/reservations', f'/orders/{uuid4()}', f'/reservations/{uuid4()}']:
            assert c.get(path).status_code == 401
        assert buy(c, uuid4()).status_code == 401


def test_two_users_cannot_read_purchase_or_replay_each_others_bookings(client, database_url):
    first_hold = hold(client, [1])
    key = uuid4()
    with anonymous(database_url) as other:
        register(other)
        assert other.get(f'/reservations/{first_hold}').status_code == 404
        assert buy(other, first_hold, key).status_code == 404
        first_order = buy(client, first_hold, key).json()
        assert other.get(f"/orders/{first_order['id']}").status_code == 404
        assert buy(other, first_hold, key).status_code == 404
        own_hold = hold(other, [2])
        # The same key is independently usable by another authenticated user.
        second_order = buy(other, own_hold, key).json()
        assert second_order['id'] != first_order['id']
        assert client.get(f"/orders/{second_order['id']}").status_code == 404
        assert [o['id'] for o in other.get('/me/orders').json()['items']] == [second_order['id']]
        assert [r['id'] for r in other.get('/me/reservations').json()['items']] == [own_hold]
        assert [o['id'] for o in client.get('/me/orders').json()['items']] == [first_order['id']]


def test_csrf_origin_and_account_switch_checks(client, database_url):
    with TestClient(create_app(database_url)) as c:
        assert c.post('/auth/login', json={'email': TEST_EMAIL, 'password': TEST_PASSWORD}).status_code == 403
        for headers in [{'Origin': 'https://evil.example', **HEADERS}, {'Sec-Fetch-Site': 'cross-site', **HEADERS}]:
            assert c.post('/auth/logout', headers=headers).status_code == 403
    response = client.post('/reservations', headers={'X-Account-ID': str(uuid4())},
                           json={'event_id': DEMO_EVENT_ID, 'seat_numbers': [1]})
    assert response.status_code == 409
    with connect(database_url) as conn:
        assert conn.execute('SELECT count(*) AS n FROM reservations').fetchone()['n'] == 0
    assert client.post('/auth/logout', headers={'Origin': 'http://testserver'}).status_code == 200


def test_expired_session_and_secure_cookie(database_url):
    with connect(database_url) as conn:
        conn.execute("UPDATE sessions SET expires_at=clock_timestamp()-interval '1 second'")
    with anonymous(database_url) as c:
        c.cookies.set(COOKIE, TEST_TOKEN)
        assert c.get('/auth/me').status_code == 401
    with TestClient(create_app(database_url), base_url='https://testserver', headers=HEADERS) as c:
        response = register(c)
        assert 'Secure' in response.headers['set-cookie']
        assert c.get('/auth/me').status_code == 200


def test_login_throttling_commits_failed_attempts(database_url):
    with anonymous(database_url) as c:
        for _ in range(10):
            assert c.post('/auth/login', json={'email': TEST_EMAIL, 'password': 'wrong-password-long'}).status_code == 401
        response = c.post('/auth/login', json={'email': TEST_EMAIL, 'password': TEST_PASSWORD})
        assert response.status_code == 429 and response.headers['retry-after'] == '900'
        with connect(database_url) as conn:
            conn.execute("UPDATE auth_buckets SET started_at=clock_timestamp()-interval '16 minutes'")
        assert c.post('/auth/login', json={'email': TEST_EMAIL, 'password': TEST_PASSWORD}).status_code == 200


def test_history_pagination_stable_under_new_inserts(client, database_url):
    ids = [buy(client, hold(client, [n])).json()['id'] for n in [1, 2, 3]]
    # Ties exercise the UUID tiebreaker.
    with connect(database_url) as conn:
        conn.execute("UPDATE orders SET created_at='2026-01-01T00:00:00Z'")
    first = client.get('/me/orders?limit=2').json()
    assert len(first['items']) == 2 and first['next_cursor']
    fresh = buy(client, hold(client, [4])).json()['id']
    second = client.get('/me/orders', params={'limit': 2, 'cursor': first['next_cursor']}).json()
    assert second['next_cursor'] is None
    combined = first['items'] + second['items']
    assert set(o['id'] for o in combined) == set(ids)
    assert fresh not in {o['id'] for o in combined}
    assert all(o['tickets'] and o['event_name'] for o in combined)
    reservations = client.get('/me/reservations?limit=2').json()
    assert len(reservations['items']) == 2 and reservations['next_cursor']
    assert all(r['status'] == 'confirmed' and r['items'] for r in reservations['items'])
    for query in ['limit=0', 'limit=51', 'cursor=invalid', 'cursor=e30=']:
        assert client.get('/me/orders?' + query).status_code == 422


def test_database_enforces_order_owner(client, database_url):
    reservation = hold(client, [1])
    with anonymous(database_url) as other:
        user = register(other).json()
    with pytest.raises(errors.ForeignKeyViolation), connect(database_url) as conn:
        conn.execute('''INSERT INTO orders(id,reservation_id,event_id,total_price_cents,currency,user_id)
                     VALUES (%s,%s,%s,7500,'USD',%s)''', (uuid4(), reservation, DEMO_EVENT_ID, user['id']))


def test_upgrade_preserves_unclaimed_legacy_purchases(database_url):
    schema = 'legacy_' + uuid4().hex
    with connect(database_url) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    legacy_url = make_conninfo(database_url, options='-csearch_path=' + schema)
    reservation_id, order_id, ticket_id, key = [uuid4() for _ in range(4)]
    try:
        with connect(legacy_url) as conn:
            conn.execute('CREATE TABLE schema_migrations (name text PRIMARY KEY)')
            for name in ['001_initial.sql', '002_checkout.sql']:
                conn.execute((Path(__file__).parent.parent / 'ticketing' / 'migrations' / name).read_text())
                conn.execute('INSERT INTO schema_migrations VALUES (%s)', (name,))
        seed(legacy_url)
        with connect(legacy_url) as conn:
            conn.execute("INSERT INTO reservations(id,event_id,expires_at) VALUES (%s,%s,clock_timestamp()+interval '5 minutes')", (reservation_id, DEMO_EVENT_ID))
            conn.execute('UPDATE event_seats SET reservation_id=%s WHERE seat_number=1', (reservation_id,))
            conn.execute('INSERT INTO reservation_items VALUES (%s,%s,1,7500)', (reservation_id, DEMO_EVENT_ID))
            conn.execute("INSERT INTO orders(id,reservation_id,event_id,total_price_cents,currency) VALUES (%s,%s,%s,7500,'USD')", (order_id, reservation_id, DEMO_EVENT_ID))
            conn.execute('INSERT INTO tickets VALUES (%s,%s,%s,%s,1,7500)', (ticket_id, order_id, reservation_id, DEMO_EVENT_ID))
            conn.execute("INSERT INTO checkout_attempts(idempotency_key,reservation_id,payment_outcome,order_id) VALUES (%s,%s,'succeeded',%s)", (key, reservation_id, order_id))
        migrate(legacy_url)
        migrate(legacy_url)
        with anonymous(legacy_url) as c:
            register(c)
            assert c.get(f'/orders/{order_id}').status_code == 404
            assert c.get(f'/reservations/{reservation_id}').status_code == 404
            assert buy(c, reservation_id, key).status_code == 404
            assert c.get('/me/orders').json()['items'] == []
            assert c.get(f'/events/{DEMO_EVENT_ID}/seats').json()[0]['status'] == 'sold'
        with connect(legacy_url) as conn:
            assert conn.execute('SELECT user_id FROM orders WHERE id=%s', (order_id,)).fetchone()['user_id'] is None
            assert conn.execute('SELECT id FROM tickets').fetchone()['id'] == ticket_id
            assert conn.execute('SELECT user_id FROM checkout_attempts').fetchone()['user_id'] is None
    finally:
        with connect(database_url) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_history_keeps_purchased_currency_snapshot(client, database_url):
    order = buy(client, hold(client, [1])).json()
    with connect(database_url) as conn:
        conn.execute("UPDATE events SET currency='EUR'")
    history = client.get('/me/orders').json()['items']
    assert history[0]['id'] == order['id']
    assert history[0]['currency'] == 'USD'
