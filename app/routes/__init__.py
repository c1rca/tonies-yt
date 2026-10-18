"""HTTP routers and access to the application's explicitly wired runtime services."""
from fastapi import Request


async def get_services(request: Request):
    # Keep the existing main.* service seams used by the frozen security tests.
    # Resolve at request time, without importing main or capturing stale bindings.
    return request.app.state.route_services
