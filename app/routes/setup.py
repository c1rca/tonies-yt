"""Health, account setup, sign-in, and credential lifecycle HTTP routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import auth
from ..credentials import VaultError
from . import get_services

router = APIRouter()


class Health(BaseModel):
    status: str


class SetupInitRequest(BaseModel):
    username: str = Field(max_length=256)
    app_password: str = Field(max_length=1024)
    tonies_email: str = Field(default="", max_length=320)
    tonies_password: str = Field(default="", max_length=1024)


class SetupLoginRequest(BaseModel):
    username: str = Field(max_length=256)
    app_password: str  # Legacy passwords are admitted within the total account-body cap.


class SetupChangePasswordRequest(BaseModel):
    username: str = Field(max_length=256)
    current_password: str
    new_password: str = Field(max_length=1024)
    creative_upload_url: str = Field(default="", max_length=2048)


class SetupToniesCredentialsRequest(BaseModel):
    username: str = Field(max_length=256)
    app_password: str
    tonies_email: str = Field(default="", max_length=320)
    tonies_password: str = Field(default="", max_length=1024)


@router.get("/api/health", response_model=Health)
def health():
    return Health(status="ok")


@router.get('/api/setup/status')
def api_setup_status(request: Request, services=Depends(get_services)):
    s = services.setup_status()
    if not auth.request_session(request):
        # Before sign-in, only say whether setup or sign-in comes next.
        return {"configured": s["configured"], "unlocked": False, "signed_in": False, "source": s["source"]}
    c = services.get_credentials()
    email = (c.get("email") or "").strip()
    return {
        **s,
        "signed_in": True,
        "app_version": services.__version__,
        "email_hint": (email[:2] + "***") if email else "",
        "tonies_email": email if s.get("unlocked") else "",
        "versions": {
            "yt_dlp": services._cmd_version(["yt-dlp", "--version"]),
            "deno": services._cmd_version(["deno", "--version"]),
        },
    }


def _signed_in_response(request: Request, body: dict, *, generation: int) -> JSONResponse:
    token = auth.create_session(expected_generation=generation)
    if token is None:
        return JSONResponse({"ok": False, "error": "Account state changed during sign-in; please sign in again"},
                            status_code=409)
    response = JSONResponse(body)
    auth.set_session_cookie(response, request, token)
    return response


def _account_result(operation, *args) -> dict:
    """Only deliberately authored vault errors cross the HTTP boundary."""
    try:
        return operation(*args)
    except VaultError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Vault storage is unavailable; check permissions or restore a known-good backup"}


def _password_busy() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Another sign-in or setup is in progress; try again shortly"},
                        status_code=429, headers={"Retry-After": "1"})


@router.post('/api/setup/init')
def api_setup_init(req: SetupInitRequest, request: Request, services=Depends(get_services)):
    with auth.password_attempt() as allowed:
        if not allowed:
            return _password_busy()
        with auth.account_transition():
            generation = auth.session_generation()
            out = _account_result(services.initialize_vault, req.username, req.app_password, req.tonies_email, req.tonies_password)
    if not out.get("ok"):
        return out
    services.reset_upload_session()
    return _signed_in_response(request, {"ok": True, **out}, generation=generation)


@router.post('/api/setup/login')
def api_setup_login(req: SetupLoginRequest, request: Request, services=Depends(get_services)):
    client = request.client.host if request.client else "unknown"
    wait = auth.login_retry_after(client)
    if wait:
        return JSONResponse({"ok": False, "error": f"Too many failed sign-ins; try again in {wait} s"},
                            status_code=429, headers={"Retry-After": str(wait)})
    with auth.password_attempt() as allowed:
        if not allowed:
            return _password_busy()
        with auth.account_transition():
            generation = auth.session_generation()
            result = _account_result(services.login_unlock, req.username, req.app_password)
    if not result.get('ok'):
        auth.record_login_failure(client)
        return result
    auth.clear_login_failures(client)
    services.reset_upload_session()
    return _signed_in_response(request, result, generation=generation)


@router.post('/api/setup/lock')
def api_setup_lock(services=Depends(get_services)):
    """Lock the vault and sign every browser out."""
    with auth.account_transition():
        result = services.lock_runtime()
        auth.revoke_all_sessions()
    services.reset_upload_session()
    response = JSONResponse(result)
    auth.clear_session_cookie(response)
    return response


@router.post('/api/setup/change-password')
def api_setup_change_password(req: SetupChangePasswordRequest, request: Request, services=Depends(get_services)):
    with auth.account_transition():
        result = _account_result(services.change_app_password, req.username, req.current_password, req.new_password)
        if result.get('ok'):
            auth.revoke_all_sessions(keep=auth.request_session(request))
    if result.get('ok'):
        services.reset_upload_session()
    return result


@router.post('/api/setup/update-tonies-credentials')
def api_setup_update_tonies_credentials(req: SetupToniesCredentialsRequest, request: Request, services=Depends(get_services)):
    with auth.account_transition():
        result = _account_result(services.update_tonies_credentials, req.username, req.app_password, req.tonies_email, req.tonies_password)
        if result.get('ok'):
            auth.revoke_all_sessions(keep=auth.request_session(request))
    if result.get('ok'):
        services.reset_upload_session()
    return result


@router.post('/api/setup/update-yt-dlp')
def api_setup_update_ytdlp():
    # Kept so 2.0 pages still open in a browser get a clear answer instead of a 404.
    return {"ok": False, "update_supported": False,
            "error": "Runtime updates are disabled. Rebuild or pull an updated container image to update yt-dlp"}
