"""Atomic checkout using a local payment simulator; no external charges."""
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ticketing.db import connect


class CheckoutRequest(BaseModel):
    payment_outcome: Literal["succeeded", "declined"] = "succeeded"


def read_order(conn, order_id):
    order = conn.execute("SELECT * FROM orders WHERE id=%s", (order_id,)).fetchone()
    if order is None:
        raise HTTPException(404, "Order not found")
    order["tickets"] = conn.execute(
        "SELECT id, event_id, seat_number, price_cents FROM tickets WHERE order_id=%s ORDER BY seat_number",
        (order_id,),
    ).fetchall()
    return order


def declined():
    # Return instead of raising: the attempt must commit for replay.
    return JSONResponse(status_code=402, content={"detail": "Simulated payment declined"})


def checkout_router(database_url=None):
    router = APIRouter()

    @router.get("/orders/{order_id}")
    def get_order(order_id: UUID):
        with connect(database_url) as conn:
            return read_order(conn, order_id)

    @router.post("/reservations/{reservation_id}/checkout")
    def checkout(reservation_id: UUID, body: CheckoutRequest,
                 idempotency_key: UUID = Header()):
        with connect(database_url) as conn:
            # Serialize this key before taking inventory locks. Hash collisions only
            # cause extra waiting; the actual UUID remains the unique database key.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (str(idempotency_key),))
            attempt = conn.execute(
                "SELECT * FROM checkout_attempts WHERE idempotency_key=%s", (idempotency_key,),
            ).fetchone()
            if attempt:
                if (attempt["reservation_id"] != reservation_id
                        or attempt["payment_outcome"] != body.payment_outcome):
                    raise HTTPException(409, "Idempotency key already used for a different request")
                return read_order(conn, attempt["order_id"]) if attempt["order_id"] else declined()

            reservation = conn.execute(
                "SELECT * FROM reservations WHERE id=%s", (reservation_id,),
            ).fetchone()
            if reservation is None:
                raise HTTPException(404, "Reservation not found")
            items = conn.execute(
                "SELECT * FROM reservation_items WHERE reservation_id=%s ORDER BY seat_number",
                (reservation_id,),
            ).fetchall()
            seats = conn.execute(
                """SELECT seat_number, reservation_id FROM event_seats
                WHERE event_id=%s AND seat_number=ANY(%s)
                ORDER BY seat_number FOR UPDATE""",
                (reservation["event_id"], [i["seat_number"] for i in items]),
            ).fetchall()
            # Read only after waiting for inventory locks, like the hold endpoint.
            if conn.execute("SELECT 1 FROM orders WHERE reservation_id=%s", (reservation_id,)).fetchone():
                raise HTTPException(409, "Reservation already purchased; retry with the original idempotency key")
            now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if (not items or len(seats) != len(items) or reservation["expires_at"] <= now
                    or any(s["reservation_id"] != reservation_id for s in seats)):
                raise HTTPException(409, "Reservation expired or no longer owns all seats")

            order_id = None
            if body.payment_outcome == "succeeded":
                order_id = uuid4()
                currency = conn.execute("SELECT currency FROM events WHERE id=%s",
                                        (reservation["event_id"],)).fetchone()["currency"]
                conn.execute(
                    """INSERT INTO orders (id, reservation_id, event_id, total_price_cents, currency)
                    VALUES (%s,%s,%s,%s,%s)""",
                    (order_id, reservation_id, reservation["event_id"],
                     sum(i["price_cents"] for i in items), currency),
                )
                for item in items:
                    conn.execute(
                        """INSERT INTO tickets (id, order_id, reservation_id, event_id, seat_number, price_cents)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                        (uuid4(), order_id, reservation_id, item["event_id"],
                         item["seat_number"], item["price_cents"]),
                    )
            conn.execute(
                """INSERT INTO checkout_attempts (idempotency_key, reservation_id, payment_outcome, order_id)
                VALUES (%s,%s,%s,%s)""",
                (idempotency_key, reservation_id, body.payment_outcome, order_id),
            )
            return read_order(conn, order_id) if order_id else declined()

    return router
