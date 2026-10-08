from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from ticketing.db import connect
from ticketing.main import create_app
from ticketing.manage import DEMO_EVENT_ID, migrate, seed


def payload(seats):
    return {"event_id": DEMO_EVENT_ID, "seat_numbers": seats}


def race(database_url, requests):
    barrier = Barrier(len(requests))
    app = create_app(database_url)

    def book(seats):
        with TestClient(app) as client:
            barrier.wait(timeout=30)
            return client.post("/reservations", json=payload(seats))

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        return list(executor.map(book, requests))


def test_seed_inventory_and_read_reservation(client, database_url):
    migrate(database_url)
    seed(database_url)  # Both operations are safe to repeat.
    assert client.get("/health").json() == {"status": "ok"}
    assert len(client.get("/events").json()) == 1
    seats = client.get(f"/events/{DEMO_EVENT_ID}/seats").json()
    assert len(seats) == 20
    assert all(s["status"] == "available" for s in seats)
    response = client.post("/reservations", json=payload([2, 1]))
    assert response.status_code == 201
    data = response.json()
    assert data["seat_numbers"] == [1, 2]
    assert data["total_price_cents"] == 15000
    details = client.get(f"/reservations/{data['id']}").json()
    assert details["status"] == "held"
    assert [i["seat_number"] for i in details["items"]] == [1, 2]
    seed(database_url)
    assert client.post("/reservations", json=payload([1])).status_code == 409


def test_100_clients_one_seat(database_url):
    responses = race(database_url, [[1]] * 100)
    codes = [r.status_code for r in responses]
    assert codes.count(201) == 1, codes
    assert codes.count(409) == 99, codes
    with connect(database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM reservations").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM reservation_items").fetchone()["n"] == 1
        winner = next(r.json()["id"] for r in responses if r.status_code == 201)
        owner = conn.execute("SELECT reservation_id FROM event_seats WHERE seat_number=1").fetchone()
        assert str(owner["reservation_id"]) == winner


def test_overlapping_groups_are_atomic(database_url):
    responses = race(database_url, [[1, 2], [3, 2]])
    assert sorted(r.status_code for r in responses) == [201, 409]
    winner = next(r.json() for r in responses if r.status_code == 201)
    with connect(database_url) as conn:
        held = conn.execute("SELECT seat_number FROM event_seats WHERE reservation_id IS NOT NULL ORDER BY seat_number").fetchall()
        assert [s["seat_number"] for s in held] == winner["seat_numbers"]
        assert conn.execute("SELECT count(*) AS n FROM reservations").fetchone()["n"] == 1


def test_expired_hold_is_reclaimed_without_worker(client, database_url):
    old = client.post("/reservations", json=payload([1])).json()
    with connect(database_url) as conn:
        conn.execute("""UPDATE reservations SET created_at=clock_timestamp()-interval '10 minutes',
                     expires_at=clock_timestamp()-interval '5 minutes' WHERE id=%s""", (old["id"],))
    assert client.get(f"/reservations/{old['id']}").json()["status"] == "expired"
    assert client.get(f"/events/{DEMO_EVENT_ID}/seats").json()[0]["status"] == "available"
    responses = race(database_url, [[1]] * 10)
    assert sorted(r.status_code for r in responses) == [201] + [409] * 9
    assert next(r.json()["id"] for r in responses if r.status_code == 201) != old["id"]
    assert len(client.get(f"/reservations/{old['id']}").json()["items"]) == 1


def test_missing_seat_rolls_back(client, database_url):
    assert client.post("/reservations", json=payload([1, 999])).status_code == 404
    with connect(database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM reservations").fetchone()["n"] == 0
    assert client.post("/reservations", json=payload([1])).status_code == 201


def test_invalid_inputs_and_missing_resources(client):
    for seats in ([], [1, 1], [0], [-1], list(range(1, 12))):
        assert client.post("/reservations", json=payload(seats)).status_code == 422
    missing = str(uuid4())
    assert client.post("/reservations", json={"event_id": missing, "seat_numbers": [1]}).status_code == 404
    assert client.get(f"/events/{missing}/seats").status_code == 404
    assert client.get(f"/reservations/{missing}").status_code == 404
    assert client.get("/events/not-a-uuid/seats").status_code == 422
