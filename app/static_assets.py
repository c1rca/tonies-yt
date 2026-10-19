"""Static assets revalidate; unchanged files reuse their cached bodies through ETags."""
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


class PageAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            # A query token is not content identity. Old/invalid URLs must not pin new bytes.
            response.headers["Cache-Control"] = "no-cache"
        return response
