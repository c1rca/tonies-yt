"""Per-browser sign-in sessions and request guards.

Signing in with the app username and password issues a random session token in an
HttpOnly, SameSite=Strict cookie. Every API route and app page needs a valid session
except the few that sign-in itself depends on. Sessions live in memory: a restart or
"Lock" ends all of them, matching the vault, which also locks on restart.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE_NAME = "tonies_yt_session"
SESSION_TTL_SEC = 30 * 24 * 3600
LOGIN_WINDOW_SEC = 300
LOGIN_MAX_FAILURES = 5

# Reachable without a session: what sign-in and first-run setup need, plus health.
PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/setup/status",
    "/api/setup/login",
    "/api/setup/init",
    "/login",
    "/setup",
    "/favicon.ico",
})
PUBLIC_PREFIXES = ("/static/",)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_lock = threading.Lock()
_sessions: dict[str, float] = {}  # token -> expiry (monotonic seconds)
_failures: dict[str, deque[float]] = {}


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = time.monotonic() + SESSION_TTL_SEC
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
    with _lock:
        kept = {keep: _sessions[keep]} if keep and keep in _sessions else {}
        _sessions.clear()
        _sessions.update(kept)


def request_session(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    return token if session_valid(token) else None


def set_session_cookie(response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL_SEC, httponly=True, samesite="strict",
                        secure=secure, path="/")


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")


def login_retry_after(client: str) -> int:
    """Seconds this client must wait before another sign-in attempt (0 = allowed)."""
    now = time.monotonic()
    with _lock:
        recent = _failures.get(client)
        if not recent:
            return 0
        while recent and recent[0] <= now - LOGIN_WINDOW_SEC:
            recent.popleft()
        if len(recent) < LOGIN_MAX_FAILURES:
            return 0
        return max(1, int(recent[0] + LOGIN_WINDOW_SEC - now))


def record_login_failure(client: str) -> None:
    with _lock:
        _failures.setdefault(client, deque(maxlen=LOGIN_MAX_FAILURES)).append(time.monotonic())


def clear_login_failures(client: str) -> None:
    with _lock:
        _failures.pop(client, None)


def reset_for_tests() -> None:
    with _lock:
        _sessions.clear()
        _failures.clear()


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _cross_site(request: Request) -> bool:
    """True for state-changing requests a browser sent from another site."""
    if request.method in SAFE_METHODS:
        return False
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        origin_host = urlsplit(origin).netloc.lower()
    except ValueError:
        return True
    hosts = {request.headers.get("host", "").lower()}
    # A reverse proxy that rewrites Host should pass the browser's host on here.
    hosts.update(h.strip().lower() for h in request.headers.get("x-forwarded-host", "").split(",") if h.strip())
    return origin_host not in hosts


class SessionGuard:
    """ASGI middleware: refuse cross-site writes and anything without a session."""

    def __init__(self, app: ASGIApp, *, is_configured) -> None:
        self.app = app
        self._is_configured = is_configured

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = request.url.path
        if _cross_site(request):
            response = JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        elif _is_public(path) or request_session(request):
            await self.app(scope, receive, send)
            return
        elif path.startswith("/api/"):
            response = JSONResponse({"detail": "Sign in required"}, status_code=401)
        else:
            response = RedirectResponse("/login" if self._is_configured() else "/setup", status_code=302)
        await response(scope, receive, send)
