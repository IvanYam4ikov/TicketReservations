from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from ticketing.db import connect


class ReservationRequest(BaseModel):
    event_id: UUID
    seat_numbers: list[int] = Field(min_length=1, max_length=10)

    @field_validator("seat_numbers")
    @classmethod
    def validate_seats(cls, seats):
        if any(n <= 0 for n in seats) or len(seats) != len(set(seats)):
            raise ValueError("Seat numbers must be positive and unique")
        return seats


def create_app(database_url=None):
    app = FastAPI(title="Ticket Reservations", version="0.1.0")

    @app.get("/health")
    def health():
        with connect(database_url) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/events")
    def events():
        with connect(database_url) as conn:
            return conn.execute("SELECT * FROM events ORDER BY starts_at, id").fetchall()

    @app.get("/events/{event_id}/seats")
    def inventory(event_id: UUID):
        with connect(database_url) as conn:
            if not conn.execute("SELECT 1 FROM events WHERE id=%s", (event_id,)).fetchone():
                raise HTTPException(404, "Event not found")
            return conn.execute(
                """SELECT s.seat_number, s.price_cents,
                CASE WHEN r.expires_at > statement_timestamp() THEN 'held'
                     ELSE 'available' END AS status
                FROM event_seats s LEFT JOIN reservations r ON r.id=s.reservation_id
                WHERE s.event_id=%s ORDER BY s.seat_number""", (event_id,),
            ).fetchall()

    @app.post("/reservations", status_code=201)
    def reserve(body: ReservationRequest):
        with connect(database_url) as conn:
            if not conn.execute("SELECT 1 FROM events WHERE id=%s", (body.event_id,)).fetchone():
                raise HTTPException(404, "Event not found")
            # All allocation paths must lock inventory in this same order.
            seats = conn.execute(
                """SELECT seat_number, price_cents, reservation_id FROM event_seats
                WHERE event_id=%s AND seat_number=ANY(%s)
                ORDER BY seat_number FOR UPDATE""", (body.event_id, body.seat_numbers),
            ).fetchall()
            if len(seats) != len(body.seat_numbers):
                raise HTTPException(404, "One or more seats do not exist")
            # Separate statement AFTER lock acquisition: fresh READ COMMITTED snapshot,
            # including reservations committed by a transaction we waited behind.
            now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            active = conn.execute(
                "SELECT 1 FROM reservations WHERE id=ANY(%s::uuid[]) AND expires_at>%s LIMIT 1",
                ([s["reservation_id"] for s in seats if s["reservation_id"]], now),
            ).fetchone()
            if active:
                raise HTTPException(409, "One or more seats are already held")
            reservation_id = uuid4()
            expires_at = now + timedelta(minutes=5)
            conn.execute(
                "INSERT INTO reservations (id, event_id, created_at, expires_at) VALUES (%s,%s,%s,%s)",
                (reservation_id, body.event_id, now, expires_at),
            )
            conn.execute(
                "UPDATE event_seats SET reservation_id=%s WHERE event_id=%s AND seat_number=ANY(%s)",
                (reservation_id, body.event_id, body.seat_numbers),
            )
            conn.execute(
                """INSERT INTO reservation_items (reservation_id, event_id, seat_number, price_cents)
                SELECT %s, event_id, seat_number, price_cents FROM event_seats
                WHERE event_id=%s AND seat_number=ANY(%s)""",
                (reservation_id, body.event_id, body.seat_numbers),
            )
        return {"id": reservation_id, "event_id": body.event_id,
                "seat_numbers": sorted(body.seat_numbers), "expires_at": expires_at,
                "total_price_cents": sum(s["price_cents"] for s in seats)}

    @app.get("/reservations/{reservation_id}")
    def reservation(reservation_id: UUID):
        with connect(database_url) as conn:
            row = conn.execute(
                """SELECT *, CASE WHEN expires_at>statement_timestamp() THEN 'held'
                ELSE 'expired' END AS status FROM reservations WHERE id=%s""", (reservation_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Reservation not found")
            row["items"] = conn.execute(
                "SELECT seat_number, price_cents FROM reservation_items WHERE reservation_id=%s ORDER BY seat_number",
                (reservation_id,),
            ).fetchall()
            return row

    return app


app = create_app()
