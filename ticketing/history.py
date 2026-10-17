"""Private history with stable (created_at, id) keyset pagination."""
import base64
from datetime import datetime
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from ticketing.db import connect


def encode_cursor(row):
    return base64.urlsafe_b64encode(json.dumps([row['created_at'].isoformat(), str(row['id'])]).encode()).decode()


def decode_cursor(cursor):
    if not cursor:
        return None
    try:
        stamp, identifier = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
        timestamp = datetime.fromisoformat(stamp)
        if timestamp.tzinfo is None:
            raise ValueError()
        return timestamp, UUID(identifier)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, 'Invalid history cursor') from None


def history_router(database_url, current_user):
    router = APIRouter(prefix='/me', tags=['history'])

    def history(kind, user_id, limit, cursor):
        position = decode_cursor(cursor)
        # kind is selected by these two routes, never by a request parameter.
        params = [user_id]
        before = ''
        if position:
            before = 'AND (h.created_at,h.id)<(%s,%s)'
            params.extend(position)
        params.append(limit + 1)
        status = """, CASE WHEN EXISTS (SELECT 1 FROM orders WHERE reservation_id=h.id)
                     THEN 'confirmed' WHEN h.expires_at>statement_timestamp() THEN 'held'
                     ELSE 'expired' END AS status""" if kind == 'reservations' else ''
        currency = ', e.currency' if kind == 'reservations' else ''
        with connect(database_url) as conn:
            rows = conn.execute(
                f'''SELECT h.*, e.name AS event_name, e.starts_at {currency} {status}
                FROM {kind} h JOIN events e ON e.id=h.event_id WHERE h.user_id=%s {before}
                ORDER BY h.created_at DESC,h.id DESC LIMIT %s''', params,
            ).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
            if rows:
                field, table, group = ('items', 'reservation_items', 'reservation_id') if kind == 'reservations' else ('tickets', 'tickets', 'order_id')
                items = conn.execute(f'SELECT * FROM {table} WHERE {group}=ANY(%s) ORDER BY seat_number',
                                     ([r['id'] for r in rows],)).fetchall()
                grouped = {row['id']: [] for row in rows}
                for item in items:
                    grouped[item[group]].append(item)
                for row in rows:
                    row[field] = grouped[row['id']]
                    if kind == 'reservations':
                        row['total_price_cents'] = sum(i['price_cents'] for i in row[field])
        return {'items': rows, 'next_cursor': encode_cursor(rows[-1]) if more else None}

    @router.get('/orders')
    def orders(limit: int = Query(default=10, ge=1, le=50),
               cursor: str | None = Query(default=None, max_length=512), user=Depends(current_user)):
        return history('orders', user['id'], limit, cursor)

    @router.get('/reservations')
    def reservations(limit: int = Query(default=10, ge=1, le=50),
                     cursor: str | None = Query(default=None, max_length=512), user=Depends(current_user)):
        return history('reservations', user['id'], limit, cursor)

    return router
