from __future__ import annotations

from .config import settings
from .models import ParseIntent


def parse_request(message: str) -> ParseIntent:
    """Backward-compatible deterministic parser used by jobs pipeline."""
    text = (message or "").strip()
    return ParseIntent(
        action="download_and_upload",
        youtube_query=text,
        target_character_name=settings.tonies_character_name or None,
        preferred_title=None,
    )


def parse_intent(message: str) -> ParseIntent:
    return parse_request(message)
