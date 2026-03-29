"""Compatibility shim for legacy imports.

Some code paths import `parse_request` from top-level `api.py`.
Keep this small deterministic parser so old imports continue to work.
"""

from __future__ import annotations


def parse_request(message: str):
    text = (message or "").strip()
    return {
        "action": "download_and_upload",
        "youtube_query": text,
        "target_character_name": None,
        "preferred_title": None,
    }
