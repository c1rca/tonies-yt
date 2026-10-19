"""Static page assets: versioned URLs (``?v=N``) are cached for good, anything else revalidates."""
from urllib.parse import parse_qs

from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

# A versioned asset never changes: a new file gets a new ?v= in the page that loads it.
VERSIONED = "public, max-age=31536000, immutable"


class PageAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            response.headers["Cache-Control"] = VERSIONED if query.get("v") else "no-cache"
        return response
