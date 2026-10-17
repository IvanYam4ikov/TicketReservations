import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from ticketing.db import connect
from ticketing.main import create_app
from ticketing.manage import migrate, seed
from ticketing.auth import COOKIE, hash_password, token_hash

TEST_TOKEN = "integration-test-session-token"
TEST_PASSWORD = "integration-password-123"
TEST_EMAIL = "fixture@example.com"
TEST_HASH = hash_password(TEST_PASSWORD)

def signed_client(app, **kwargs):
    return TestClient(app, headers={"X-Requested-With": "FrontRow"},
                      cookies={COOKIE: TEST_TOKEN}, **kwargs)


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
        with connect(url) as conn:
            user = conn.execute("INSERT INTO users (id,email,password_hash) VALUES (%s,%s,%s) RETURNING id",
                                (uuid4(), TEST_EMAIL, TEST_HASH)).fetchone()
            conn.execute("INSERT INTO sessions (token_hash,user_id,expires_at) VALUES (%s,%s,clock_timestamp()+interval '1 day')",
                         (token_hash(TEST_TOKEN), user['id']))
        yield url
    finally:
        with connect(base) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def client(database_url):
    with signed_client(create_app(database_url)) as client:
        yield client
