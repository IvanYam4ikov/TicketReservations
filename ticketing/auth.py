"""Password authentication and opaque, revocable database sessions."""
import hashlib
import hmac
import os
import re
import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.security import APIKeyCookie
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field, field_validator
from ticketing.db import connect

COOKIE = 'fr_session'
SESSION_SECONDS = 7 * 24 * 60 * 60
cookie_scheme = APIKeyCookie(name=COOKIE, auto_error=False)


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return f'scrypt$32768$8$3${salt.hex()}${digest.hex()}'


def check_password(password, encoded):
    return hmac.compare_digest(hash_password(password, bytes.fromhex(encoded.split('$')[4])), encoded)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# Equal-cost verification for a nonexistent account; never used as a real credential.
DUMMY_HASH = hash_password('dummy-password-not-a-user')


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Enter a valid email address')
        return value


def protect_mutation(request: Request, x_requested_with: str | None = Header(default=None)):
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    # No CORS is enabled: another origin cannot send this header with cookies.
    origin = request.headers.get('origin')
    if (x_requested_with != 'FrontRow'
            or (origin is not None and origin != str(request.base_url).rstrip('/'))
            or request.headers.get('sec-fetch-site') == 'cross-site'):
        raise HTTPException(403, 'Same-origin request with X-Requested-With: FrontRow required')


def user_dependency(database_url=None):
    def current_user(request: Request, token: str | None = Depends(cookie_scheme)):
        if not token or len(token) > 256:
            raise HTTPException(401, 'Please sign in')
        with connect(database_url) as conn:
            user = conn.execute(
                '''SELECT u.id, u.email FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=%s AND s.expires_at>clock_timestamp()''', (token_hash(token),),
            ).fetchone()
        if user is None:
            raise HTTPException(401, 'Session expired. Please sign in again')
        expected = request.headers.get('x-account-id')
        if expected and expected != str(user['id']):
            raise HTTPException(409, 'Account changed. Reload before continuing')
        return user
    return current_user


def rate_limit(database_url, key, limit):
    # Commit independently of failed authentication; serialize concurrent attempts.
    with connect(database_url) as conn:
        count = conn.execute(
            '''INSERT INTO auth_buckets (key, attempts) VALUES (%s,1)
            ON CONFLICT (key) DO UPDATE SET
            attempts=CASE WHEN auth_buckets.started_at < clock_timestamp()-interval '15 minutes'
                          THEN 1 ELSE auth_buckets.attempts+1 END,
            started_at=CASE WHEN auth_buckets.started_at < clock_timestamp()-interval '15 minutes'
                            THEN clock_timestamp() ELSE auth_buckets.started_at END
            RETURNING attempts''', (token_hash(key),),
        ).fetchone()['attempts']
    if count > limit:
        raise HTTPException(429, 'Too many attempts. Try again in 15 minutes', headers={'Retry-After': '900'})


def start_session(conn, user_id, request, response):
    old = request.cookies.get(COOKIE)
    if old:
        conn.execute('DELETE FROM sessions WHERE token_hash=%s', (token_hash(old),))
    token = secrets.token_urlsafe(32)
    conn.execute('''INSERT INTO sessions (token_hash,user_id,expires_at)
                 VALUES (%s,%s,clock_timestamp()+interval '7 days')''', (token_hash(token), user_id))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == 'https' or os.getenv('COOKIE_SECURE') == 'true',
                        samesite='strict', path='/')


def auth_router(database_url, current_user):
    router = APIRouter(prefix='/auth', tags=['accounts'])

    @router.post('/register', status_code=201)
    def register(body: Credentials, request: Request, response: Response):
        rate_limit(database_url, 'register:' + (request.client.host if request.client else 'unknown'), 30)
        encoded = hash_password(body.password)
        try:
            with connect(database_url) as conn:
                user = conn.execute('INSERT INTO users (id,email,password_hash) VALUES (%s,%s,%s) RETURNING id,email',
                                    (uuid4(), body.email, encoded)).fetchone()
                start_session(conn, user['id'], request, response)
        except UniqueViolation:
            raise HTTPException(409, 'An account with this email already exists') from None
        return user

    @router.post('/login')
    def login(body: Credentials, request: Request, response: Response):
        rate_limit(database_url, 'login:' + body.email, 10)
        with connect(database_url) as conn:
            user = conn.execute('SELECT id,email,password_hash FROM users WHERE email=%s', (body.email,)).fetchone()
            valid = check_password(body.password, user['password_hash'] if user else DUMMY_HASH)
            if not user or not valid:
                raise HTTPException(401, 'Invalid email or password')
            start_session(conn, user['id'], request, response)
        return {'id': user['id'], 'email': user['email']}

    @router.get('/me')
    def me(user=Depends(current_user)):
        return user

    @router.post('/logout')
    def logout(request: Request, response: Response):
        with connect(database_url) as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=%s', (token_hash(request.cookies.get(COOKIE, '')),))
        response.delete_cookie(COOKIE, path='/', httponly=True, samesite='strict')
        return {'status': 'signed out'}

    return router
