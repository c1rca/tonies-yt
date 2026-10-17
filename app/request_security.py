"""HTTP host/origin validation and parser-independent bounded body admission."""
from __future__ import annotations

import tempfile
import asyncio
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
BODY_READ_TIMEOUT_SEC = 120

def _origin(value: str) -> tuple[str, str, int]:
    """Parse an origin, never a URL with userinfo, path or arbitrary scheme."""
    if not value or any(char.isspace() for char in value) or any(char in value for char in "\\?#"):
        raise ValueError("Invalid origin")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Invalid origin")
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


def _host_allowed(request: Request) -> bool:
    hosts = request.headers.getlist("host")
    if len(hosts) != 1:
        return False
    try:
        _, hostname, _ = _origin("http://" + hosts[0])
    except ValueError:
        return False
    return hostname in settings.allowed_hosts


def _cross_site(request: Request) -> bool:
    if request.method in SAFE_METHODS:
        return False
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origins = request.headers.getlist("origin")
    if not origins:
        # CLI clients do not send Origin. Browser cross-site writes are rejected
        # by Fetch Metadata and exact Origin checking (including setup/sign-in).
        return False
    if len(origins) != 1:
        return True
    try:
        expected = settings.public_url or f"{request.url.scheme}://{request.headers['host']}"
        return _origin(origins[0]) != _origin(expected)
    except ValueError:
        return True


async def _bounded_body(app, scope, receive, send, limit: int) -> None:
    """Bound even chunked requests before JSON/multipart parsers can buffer them.

    Spool at most 1MiB in RAM; excess uses the operator's TMPDIR. The authenticated
    upload is replayed incrementally, with a hard total cap including multipart.
    """
    request = Request(scope)
    lengths = request.headers.getlist("content-length")
    if lengths:
        try:
            if len(lengths) != 1 or int(lengths[0]) < 0:
                raise ValueError
            if int(lengths[0]) > limit:
                await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
                return
        except ValueError:
            await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
            return
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
        try:
            async with asyncio.timeout(BODY_READ_TIMEOUT_SEC):
                total = await _receive_body(receive, body, limit)
        except TimeoutError:
            await JSONResponse({"detail": "Request body timed out"}, status_code=408)(scope, receive, send)
            return
        except _BodyTooLarge:
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
            return
        if total is None:
            return
        body.seek(0)
        remaining = total
        async def replay():
            nonlocal remaining
            if remaining:
                chunk = body.read(min(65536, remaining))
                remaining -= len(chunk)
                return {"type": "http.request", "body": chunk, "more_body": remaining > 0}
            return {"type": "http.request", "body": b"", "more_body": False}
        await app(scope, replay, send)


class _BodyTooLarge(Exception):
    pass


async def _receive_body(receive, body, limit: int) -> int | None:
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            raise _BodyTooLarge
        body.write(chunk)
        if not message.get("more_body", False):
            return total


