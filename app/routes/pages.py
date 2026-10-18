"""HTML page responses, account-page redirects, and favicon delivery."""
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

from .. import __version__, auth
from ..credentials import setup_status, vault_configured
from ..web_pages import page_response

router = APIRouter()


def _nocache_file(path: str):
    return page_response(path, version=__version__)


@router.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse('web/favicon.ico', media_type='image/x-icon')


@router.get('/logs')
def logs_page():
    return _nocache_file('web/logs.html')


@router.get('/setup')
def setup_page(request: Request):
    if vault_configured():
        return RedirectResponse(url='/' if auth.request_session(request) else '/login', status_code=302)
    return _nocache_file('web/setup.html')


@router.get('/login')
def login_page(request: Request):
    if not vault_configured():
        return RedirectResponse(url='/setup', status_code=302)
    if auth.request_session(request) and setup_status().get('unlocked'):
        return RedirectResponse(url='/', status_code=302)
    return _nocache_file('web/login.html')


@router.get('/settings')
def settings_page():
    return _nocache_file('web/account.html')


@router.get('/account')
def account_page_redirect():
    return RedirectResponse(url='/settings', status_code=302)


@router.get("/")
def index():
    # SessionGuard has already sent visitors without a session to /setup or /login.
    if not setup_status().get('unlocked'):
        return RedirectResponse(url='/login', status_code=302)
    return _nocache_file("web/index.html")
