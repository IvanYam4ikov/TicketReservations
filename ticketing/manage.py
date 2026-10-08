"""Transactional SQL migrations and repeatable demo data."""
import argparse
from pathlib import Path
from ticketing.db import connect

DEMO_EVENT_ID = "11111111-1111-4111-8111-111111111111"


def migrate(database_url=None):
    with connect(database_url) as conn:
        # Serialize concurrent startup migration runners.
        conn.execute("SELECT pg_advisory_xact_lock(74201931)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
        for path in sorted((Path(__file__).resolve().parent / "migrations").glob("*.sql")):
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (path.name,)).fetchone():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))


def seed(database_url=None):
    with connect(database_url) as conn:
        conn.execute(
            """INSERT INTO events VALUES (%s, 'Database Live: Opening Night',
            '2030-06-01 19:00:00+00', 'USD') ON CONFLICT DO NOTHING""", (DEMO_EVENT_ID,)
        )
        conn.execute(
            """INSERT INTO event_seats (event_id, seat_number, price_cents)
            SELECT %s, n, 7500 FROM generate_series(1, 20) AS n ON CONFLICT DO NOTHING""",
            (DEMO_EVENT_ID,),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "seed"])
    args = parser.parse_args()
    {"migrate": migrate, "seed": seed}[args.command]()
