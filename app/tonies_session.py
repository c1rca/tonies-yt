"""Own the in-memory Tonies login, token refresh and HTTP session lifecycle."""
from __future__ import annotations

import math
import time
from threading import Lock, RLock

import httpx

API_BASE = "https://api.prod.tcs.toys/v2"
TOKEN_URL = "https://login.tonies.com/auth/realms/tonies/protocol/openid-connect/token"
_TIMEOUT = httpx.Timeout(30, connect=10, write=120, pool=10)


def checked_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise RuntimeError(f"Invalid Tonies {label}")
    return value


class ToniesSession:
    def __init__(self, email: str, password: str, *, client: httpx.Client | None = None,
                 poll_timeout: float = 8, poll_interval: float = 0.25):
        self._email, self._password = email, password
        self._client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=False, trust_env=False)
        self._access_token = self._refresh_token = ""
        self._expires_at = 0.0
        self._auth_lock = RLock()
        self._locks_lock = Lock()
        self._target_locks: dict[tuple[str, str], RLock] = {}
        self._closed = False
        self._poll_timeout, self._poll_interval = poll_timeout, poll_interval

    @property
    def closed(self) -> bool:
        """True once the session was ended (lock, sign-out or account change); never reused."""
        return self._closed

    def close(self):
        with self._auth_lock:
            self._closed = True
            self._access_token = self._refresh_token = self._email = self._password = ""
            self._expires_at = 0
            self._client.close()

    def _request(self, method, url, *, storage=False, **kwargs):
        # httpx.Client is thread-safe: reads, storage transfers and writes run concurrently.
        if self._closed:
            raise RuntimeError("Tonies account session was reset; retry with current credentials")
        try:
            request = self._client.build_request(method, url, timeout=_TIMEOUT, **kwargs)
            # No client defaults/cookies/auth may leak into the signed storage POST.
            if storage:
                for header in ("authorization", "cookie"):
                    request.headers.pop(header, None)
            return self._client.send(request, auth=None, follow_redirects=False)
        except (httpx.HTTPError, RuntimeError):
            if self._closed:
                raise RuntimeError("Tonies account session was reset; retry with current credentials") from None
            suffix = "; write outcome unconfirmed; refresh before another action" if method == "PATCH" else ""
            raise RuntimeError(f"Tonies HTTP transport failed{suffix}") from None

    @staticmethod
    def _json(response, operation):
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Tonies {operation} failed (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f"Invalid Tonies {operation} response") from None
        if not isinstance(data, dict):
            raise RuntimeError(f"Invalid Tonies {operation} response")
        return data

    def _token(self):
        with self._auth_lock:
            if self._closed:
                raise RuntimeError("Tonies account session was reset; retry with current credentials")
            if self._access_token and time.monotonic() < self._expires_at:
                return self._access_token
            form = {"client_id": "my-tonies"}
            if self._refresh_token:
                form.update(grant_type="refresh_token", refresh_token=self._refresh_token)
            else:
                form.update(grant_type="password", scope="openid", username=self._email, password=self._password)
            response = self._request("POST", TOKEN_URL, data=form)
            if form["grant_type"] == "refresh_token" and response.status_code in (400, 401):
                # Explicit refresh rejection is not an ambiguous mutation. Reauthenticate once.
                self._access_token = self._refresh_token = ""
                response = self._request("POST", TOKEN_URL, data={"client_id": "my-tonies", "grant_type": "password", "scope": "openid", "username": self._email, "password": self._password})
            data = self._json(response, "authentication")
            if not isinstance(data.get("access_token"), str) or not data["access_token"]:
                raise RuntimeError("Invalid Tonies authentication response")
            lifetime = checked_number(data.get("expires_in"), "token lifetime")
            refresh = data.get("refresh_token", self._refresh_token)
            if not isinstance(refresh, str):
                raise RuntimeError("Invalid Tonies authentication response")
            self._access_token, self._refresh_token = data["access_token"], refresh
            self._expires_at = time.monotonic() + max(0, lifetime - min(15, lifetime / 10))
            return self._access_token

    def _api(self, method: str, path: str, payload: dict, *, read=False) -> dict:
        token = self._token()
        response = self._request(method, API_BASE + path, json=payload, headers={"Authorization": f"Bearer {token}"})
        if read and response.status_code == 401:
            with self._auth_lock:
                if self._access_token == token:
                    self._expires_at = 0
                token = self._token()
            response = self._request(method, API_BASE + path, json=payload, headers={"Authorization": f"Bearer {token}"})
        return self._json(response, "API request")
