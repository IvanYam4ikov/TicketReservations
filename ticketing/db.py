import os
import psycopg
from psycopg.rows import dict_row


def connect(database_url=None):
    return psycopg.connect(
        database_url or os.environ.get(
            "DATABASE_URL", "postgresql://ticketing:ticketing@localhost:5433/ticketing"
        ),
        row_factory=dict_row,
        connect_timeout=5,
    )
