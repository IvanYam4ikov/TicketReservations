import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from ticketing.db import connect
from ticketing.main import create_app
from ticketing.manage import migrate, seed


@pytest.fixture
def database_url():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.fail("Set TEST_DATABASE_URL to a PostgreSQL database; tests create isolated schemas.")
    schema = "test_" + uuid4().hex
    with connect(base) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    url = make_conninfo(base, options=f"-csearch_path={schema}")
    try:
        migrate(url)
        seed(url)
        yield url
    finally:
        with connect(base) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def client(database_url):
    with TestClient(create_app(database_url)) as client:
        yield client
