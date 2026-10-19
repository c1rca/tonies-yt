"""Application composition, lifecycle, security middleware, and exception handling."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__, auth
from .config import settings
from .credentials import vault_configured
from .job_pipeline import start_worker
from .logger import setup_logging
from .routes import events, files, jobs, pages, setup, tonies
from .static_assets import PageAssets

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Starting Tonies-YT %s | log_file=%s | log_level=%s", __version__, settings.log_file, settings.log_level)
    start_worker()
    yield


app = FastAPI(title="Tonies-YT", version=__version__, lifespan=lifespan)
app.add_middleware(auth.SessionGuard, is_configured=vault_configured)

app.mount("/static", PageAssets(directory="web"), name="static")


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    # Default errors echo request values, including passwords and signed URLs.
    return JSONResponse({"detail": "Invalid request fields or JSON body"}, status_code=422)


for router in (setup.router, jobs.router, files.router, tonies.router, events.router, pages.router):
    app.include_router(router)
