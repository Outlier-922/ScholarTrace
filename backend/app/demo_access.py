"""Opt-in single-workspace demo guard. This is not multi-user isolation."""
from __future__ import annotations

import base64
import binascii
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.responses import JSONResponse


def enabled():
    value = os.getenv('SCHOLARTRACE_DEMO_MODE', 'false').lower()
    if value not in {'true', 'false'}:
        raise ValueError('invalid_demo_mode')
    return value == 'true'


def read_only():
    return enabled() and os.getenv('SCHOLARTRACE_DEMO_ALLOW_WRITES', 'false').lower() != 'true'


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('demo_password_length_12_to_256')
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 600_000).hex()
    return f'pbkdf2_sha256$600000${salt}${value}'


def verify_password(password, encoded):
    _, rounds, salt, expected = encoded.split('$')
    value = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(rounds)).hex()
    return hmac.compare_digest(value, expected)


def integer(name, default, minimum, maximum):
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError('invalid_demo_limit:' + name)
    return value


def authority(value, scheme):
    parsed = urlsplit(scheme + '://' + value)
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError('invalid_demo_host')
    return (parsed.hostname or '').lower(), parsed.port or (443 if scheme == 'https' else 80)


@dataclass(frozen=True)
class Settings:
    origin: str
    username: str
    encoded_password: str
    writes: bool
    requests_per_minute: int
    body_bytes: int
    max_mutations: int
    failed_logins_per_minute: int

    @classmethod
    def from_env(cls):
        origin = os.getenv('SCHOLARTRACE_DEMO_ORIGIN', '').rstrip('/')
        parsed = urlsplit(origin)
        valid = (parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'})
        if not valid or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('invalid_demo_origin')
        authority(parsed.netloc, parsed.scheme)  # Validate port as well.
        username = os.getenv('SCHOLARTRACE_DEMO_USERNAME', '')
        encoded = os.getenv('SCHOLARTRACE_DEMO_PASSWORD_HASH', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username):
            raise ValueError('invalid_demo_username')
        if not re.fullmatch(r'pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}', encoded):
            raise ValueError('invalid_demo_password_hash')
        if not os.getenv('SCHOLARTRACE_DATA_DIR', '').strip():
            raise ValueError('demo_requires_explicit_data_directory')
        return cls(origin, username, encoded, not read_only(),
                   integer('SCHOLARTRACE_DEMO_REQUESTS_PER_MINUTE', 360, 10, 10000),
                   integer('SCHOLARTRACE_DEMO_MAX_REQUEST_MB', 110, 1, 200) * 1024 * 1024,
                   integer('SCHOLARTRACE_DEMO_MAX_MUTATIONS', 2, 1, 8),
                   integer('SCHOLARTRACE_DEMO_FAILED_LOGINS_PER_MINUTE', 10, 3, 100))


class DemoGuard:
    """Pure ASGI guard preserves streamed PDF/SSE responses and bounds input."""
    def __init__(self, app, settings=None):
        self.app = app
        self.settings = settings or Settings.from_env()
        self.lock = threading.Lock()
        self.failed = OrderedDict()
        self.requests = []
        self.active_mutations = 0
        self.valid_authorization = None

    def authenticated(self, headers):
        value = headers.get(b'authorization', b'')
        if len(value) > 2048:
            return False
        signature = hashlib.sha256(value).digest()
        if self.valid_authorization is not None and hmac.compare_digest(signature, self.valid_authorization):
            return True
        try:
            scheme, encoded = value.split(b' ', 1)
            if scheme.lower() != b'basic':
                return False
            username, password = base64.b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
            if len(password) > 256:
                return False
            matches_password = verify_password(password, self.settings.encoded_password)
            matches_user = hmac.compare_digest(username.encode(), self.settings.username.encode())
            if matches_user and matches_password:
                self.valid_authorization = signature
                return True
        except (ValueError, UnicodeError, binascii.Error):
            pass
        return False

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
                return
            await self.app(scope, receive, send)
            return
        settings = self.settings
        headers = dict(scope.get('headers', []))
        method, path = scope['method'], scope['path']
        async def respond(code, detail, extra=None):
            response = JSONResponse({'detail': detail}, status_code=code, headers={
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', **(extra or {})})
            await response(scope, receive, send)
        parsed = urlsplit(settings.origin)
        try:
            valid_host = authority(headers.get(b'host', b'').decode('ascii'), parsed.scheme) == authority(parsed.netloc, parsed.scheme)
        except (ValueError, UnicodeError):
            valid_host = False
        if not valid_host:
            await respond(421, '演示访问域名不匹配。')
            return
        if path == '/healthz' and method in {'GET', 'HEAD'}:
            await JSONResponse({'status': 'ok'})(scope, receive, send)
            return
        peer = (scope.get('client') or ('unknown',))[0]
        now = time.monotonic()
        with self.lock:
            attempts = [stamp for stamp in self.failed.get(peer, []) if now - stamp < 60]
            locked = len(attempts) >= settings.failed_logins_per_minute
        if locked:
            await respond(429, '认证尝试过多，请稍后重试。', {'Retry-After': '60'})
            return
        if not self.authenticated(headers):
            # Missing credentials are a normal browser challenge, not a failed password.
            if headers.get(b'authorization'):
                with self.lock:
                    self.failed[peer] = [*attempts, now]
                    self.failed.move_to_end(peer)
                    while len(self.failed) > 1024:
                        self.failed.popitem(last=False)
            await respond(401, '请使用演示账号登录。', {'WWW-Authenticate': 'Basic realm="ScholarTrace demo", charset="UTF-8"'})
            return
        if any(part.startswith('.') for part in path.split('/') if part):
            await respond(404, '资源不存在。')
            return
        origin = headers.get(b'origin')
        if origin and origin != settings.origin.encode() or headers.get(b'sec-fetch-site') == b'cross-site':
            await respond(403, '不允许跨站访问演示工作区。')
            return
        mutation = method not in {'GET', 'HEAD', 'OPTIONS'}
        if mutation and not settings.writes:
            await respond(403, '当前演示为只读模式，不能提交、修改或删除。')
            return
        if mutation and not origin and headers.get(b'x-scholartrace-client') != b'api':
            await respond(403, '操作需要同源请求或显式API客户端标记。')
            return
        raw_length = headers.get(b'content-length')
        try:
            if raw_length and (int(raw_length) < 0 or int(raw_length) > settings.body_bytes):
                await respond(413, '请求体超过演示限制。')
                return
        except ValueError:
            await respond(400, '无效的请求长度。')
            return
        with self.lock:
            self.requests = [stamp for stamp in self.requests if now - stamp < 60]
            busy = len(self.requests) >= settings.requests_per_minute or mutation and self.active_mutations >= settings.max_mutations
            if not busy:
                self.requests.append(now)
                if mutation:
                    self.active_mutations += 1
        if busy:
            await respond(429, '演示请求过多，请稍后重试。', {'Retry-After': '60'})
            return
        received = 0
        async def limited_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > settings.body_bytes:
                    raise HTTPException(413, '请求体超过演示限制。')
            return message
        async def protected_send(message):
            if message['type'] == 'http.response.start':
                message = dict(message)
                previous = [(k, v) for k, v in message.get('headers', []) if k.lower() not in {b'cache-control', b'x-frame-options', b'referrer-policy', b'x-content-type-options'}]
                message['headers'] = previous + [(b'cache-control', b'no-store'), (b'x-frame-options', b'DENY'), (b'referrer-policy', b'no-referrer'), (b'x-content-type-options', b'nosniff')]
            await send(message)
        try:
            await self.app(scope, limited_receive, protected_send)
        finally:
            if mutation:
                with self.lock:
                    self.active_mutations -= 1


def reserve_model(tokens):
    """Persistent UTC-hour attempt/output reservations, shared by all tools."""
    if not enabled():
        return
    from . import store
    calls_limit = integer('SCHOLARTRACE_DEMO_MODEL_CALLS_PER_HOUR', 0, 0, 1000)
    tokens_limit = integer('SCHOLARTRACE_DEMO_MODEL_TOKENS_PER_HOUR', 0, 0, 2_000_000)
    if read_only() or calls_limit == 0 or tokens_limit == 0:
        raise ValueError('demo_model_budget_exhausted')
    period = int(time.time() // 3600)
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS demo_model_usage(period INTEGER PRIMARY KEY,calls INTEGER NOT NULL,tokens INTEGER NOT NULL)')
        row = conn.execute('SELECT calls,tokens FROM demo_model_usage WHERE period=?', (period,)).fetchone()
        calls, reserved = tuple(row) if row else (0, 0)
        if calls >= calls_limit or reserved + tokens > tokens_limit:
            raise ValueError('demo_model_budget_exhausted')
        conn.execute('INSERT INTO demo_model_usage VALUES(?,?,?) ON CONFLICT(period) DO UPDATE SET calls=excluded.calls,tokens=excluded.tokens', (period, calls + 1, reserved + tokens))
