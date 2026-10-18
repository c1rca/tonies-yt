"""Application composition, lifecycle, security middleware, and runtime service bindings."""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth
from .config import settings
from .credentials import (
    change_app_password as change_app_password,
    get_credentials as get_credentials,
    initialize_vault as initialize_vault,
    lock_runtime as lock_runtime,
    login_unlock as login_unlock,
    setup_status as setup_status,
    update_tonies_credentials as update_tonies_credentials,
    vault_configured,
)
from .job_pipeline import start_worker
from .job_upload import create_upload_only_job as create_upload_only_job
from .logger import setup_logging
from .media_files import store_upload as store_upload
from .routes import events, files, jobs, pages, setup, tonies
from .subprocess_runner import run_bounded
from .uploader import (
    action_queue as action_queue,
    delete_all_tonies_content as delete_all_tonies_content,
    delete_tonies_chapter as delete_tonies_chapter,
    get_tonies_content as get_tonies_content,
    list_creative_tonies as list_creative_tonies,
    rename_tonies_chapter as rename_tonies_chapter,
    reorder_tonies_chapter as reorder_tonies_chapter,
    reset_upload_session as reset_upload_session,
)

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Starting Tonies-YT %s | log_file=%s | log_level=%s", __version__, settings.log_file, settings.log_level)
    start_worker()
    yield


app = FastAPI(title="Tonies-YT", version=__version__, lifespan=lifespan)
app.add_middleware(auth.SessionGuard, is_configured=vault_configured)

app.mount("/static", StaticFiles(directory="web"), name="static")


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    # Default errors echo request values, including passwords and signed URLs.
    return JSONResponse({"detail": "Invalid request fields or JSON body"}, status_code=422)

# Live bindings preserve the existing main.* security-test seams. Routers receive
# this namespace through a FastAPI dependency, never a circular import of main.
app.state.route_services = sys.modules[__name__]


VERSION_PROBE_TIMEOUT_SEC = 12
VERSION_PROBE_OUTPUT_BYTES = 4096


def _cmd_version(cmd: list[str]) -> str:
    """First stdout line of a tool's --version; stderr is never read or shown."""
    try:
        output = run_bounded(cmd, operation="Version probe", timeout=VERSION_PROBE_TIMEOUT_SEC,
                             max_output_bytes=VERSION_PROBE_OUTPUT_BYTES)
    except RuntimeError:
        return "unknown"
    lines = output.decode("utf-8", "replace").strip().splitlines()
    return lines[0].strip()[:80] if lines else "unknown"


for router in (setup.router, jobs.router, files.router, tonies.router, events.router, pages.router):
    app.include_router(router)
