"""Single-process browser sessions and explicit HTTP trust boundaries."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import secrets
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings
from .request_security import SAFE_METHODS, _host_allowed, _cross_site, _bounded_body

COOKIE_NAME = "tonies_yt_session"
SESSION_TTL_SEC = 12 * 3600
MAX_SESSIONS = 128
LOGIN_WINDOW_SEC = 300
LOGIN_MAX_FAILURES = 5
MAX_LOGIN_CLIENTS = 1024
AUTH_BODY_LIMIT = 16 * 1024
MULTIPART_OVERHEAD_LIMIT = 1024 * 1024

PUBLIC_PATHS = frozenset({
    "/api/health", "/api/setup/status", "/api/setup/login", "/api/setup/init",
    "/login", "/setup", "/favicon.ico", "/static/login.html", "/static/setup.html",
})
_lock = threading.Lock()
_password_lock = threading.Lock()
_account_lock = threading.Lock()
_upload_slots = threading.BoundedSemaphore(2)
_sessions: dict[str, float] = {}
_failures: dict[str, deque[float]] = {}
_session_generation = 0


@contextmanager
def account_transition():
    """Order HTTP verify/unlock against credential mutation and vault locking.

    Public password admission precedes this lock. Vault/runtime and session locks
    may be taken inside it, but uploader/client/queue reset must happen outside:
    uploader acquisition can read runtime credentials in the opposite direction.
    """
    with _account_lock:
        yield


def session_generation() -> int:
    """Capture inside account_transition, before password verification/unlock."""
    with _lock:
        return _session_generation


def create_session(*, expected_generation: int | None = None) -> str | None:
    """Atomically refuse issuance if verification predates a revocation.

    None is the unguarded internal/test policy; HTTP issuance requires a generation.
    The comparison and insertion share the same lock as revocation, never a
    check-then-create gap. No vault or uploader work is performed under this lock.
    """
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        if expected_generation is not None and expected_generation != _session_generation:
            return None
        for stale in [key for key, expiry in _sessions.items() if expiry <= now]:
            del _sessions[stale]
        while len(_sessions) >= MAX_SESSIONS:
            del _sessions[next(iter(_sessions))]
        _sessions[token] = now + SESSION_TTL_SEC
    return token


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    with _lock:
        expiry = _sessions.get(token)
        if expiry is None:
            return False
        if expiry <= time.monotonic():
            del _sessions[token]
            return False
        return True


def revoke_all_sessions(keep: str | None = None) -> None:
    """Revoke issued sessions AND pending issuance; retain the initiator if valid."""
    global _session_generation
    with _lock:
        _session_generation += 1
        kept = {keep: _sessions[keep]} if keep and keep in _sessions else {}
        _sessions.clear()
        _sessions.update(kept)


def request_session(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    return token if session_valid(token) else None


def set_session_cookie(response, request: Request, token: str) -> None:
    # Raw forwarding headers are untrusted. Uvicorn may set the ASGI scheme only
    # for operator-configured proxy IPs; a canonical public URL is explicit too.
    secure = request.url.scheme == "https" or settings.public_url.startswith("https://")
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL_SEC, httponly=True,
                        samesite="strict", secure=secure, path="/")


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")


def _prune_failures(now: float) -> None:
    for client, recent in list(_failures.items()):
        while recent and recent[0] <= now - LOGIN_WINDOW_SEC:
            recent.popleft()
        if not recent:
            del _failures[client]


def login_retry_after(client: str) -> int:
    now = time.monotonic()
    with _lock:
        _prune_failures(now)
        recent = _failures.get(client)
        if not recent or len(recent) < LOGIN_MAX_FAILURES:
            return 0
        return max(1, int(recent[0] + LOGIN_WINDOW_SEC - now))


def record_login_failure(client: str) -> None:
    with _lock:
        _prune_failures(time.monotonic())
        if client not in _failures and len(_failures) >= MAX_LOGIN_CLIENTS:
            del _failures[next(iter(_failures))]
        _failures.setdefault(client, deque(maxlen=LOGIN_MAX_FAILURES)).append(time.monotonic())


def clear_login_failures(client: str) -> None:
    with _lock:
        _failures.pop(client, None)


def reset_for_tests() -> None:
    global _session_generation
    with _lock:
        _session_generation += 1
        _sessions.clear()
        _failures.clear()


@contextmanager
def password_attempt():
    """Do not queue untrusted expensive password work on application threads."""
    acquired = _password_lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _password_lock.release()


def _is_public(path: str) -> bool:
    # Dot segments would let /static/assets/../index.html reach pages outside assets/.
    if any(segment in (".", "..") for segment in path.split("/")):
        return False
    return path in PUBLIC_PATHS or path.startswith("/static/assets/")


class SessionGuard:
    def __init__(self, app: ASGIApp, *, is_configured) -> None:
        self.app = app
        self._is_configured = is_configured

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = scope["path"]
        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "same-origin"
                if not path.startswith("/static/assets/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)
        if not _host_allowed(request):
            response = JSONResponse({"detail": "Host not allowed"}, status_code=400)
        elif _cross_site(request):
            response = JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        elif _is_public(path) or request_session(request):
            if request.method not in SAFE_METHODS:
                limit = AUTH_BODY_LIMIT if path.startswith("/api/setup/") else 1024 * 1024
                if path == "/api/files/upload":
                    limit = settings.max_upload_bytes + MULTIPART_OVERHEAD_LIMIT
                    if not _upload_slots.acquire(blocking=False):
                        await JSONResponse({"detail": "Audio uploads are busy; try again shortly"},
                                           status_code=429, headers={"Retry-After": "5"})(scope, receive, protected_send)
                        return
                    try:
                        await _bounded_body(self.app, scope, receive, protected_send, limit)
                    finally:
                        _upload_slots.release()
                else:
                    await _bounded_body(self.app, scope, receive, protected_send, limit)
            else:
                await self.app(scope, receive, protected_send)
            return
        elif path.startswith("/api/"):
            response = JSONResponse({"detail": "Sign in required"}, status_code=401)
        else:
            response = RedirectResponse("/login" if self._is_configured() else "/setup", status_code=302)
        await response(scope, receive, protected_send)
