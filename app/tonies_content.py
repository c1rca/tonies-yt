"""Own Tonies target URLs, inventory validation, content reads and write read-back."""
from __future__ import annotations

import copy
import math
import re
import time
from urllib.parse import urlsplit

from .tonies_session import ToniesSession, _number

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_TARGET = re.compile(rf"/creative-tonies/({_UUID})/([0-9a-fA-F]{{16}})/edit")
_TRACK = re.compile(r"\s\[(?:oc:)?([a-f0-9]{4,12})\]$", re.I)
QUERY = """query ($householdId: String, $creativeTonieId: String) {
  config { maxChapters maxSeconds maxBytes accepts }
  households(id: $householdId) { id name
    creativeTonies(id: $creativeTonieId) {
      id name imageUrl live transcoding secondsRemaining
      chapters { id title file seconds transcoding type }
    }
  }
}"""


def parse_target_url(value: str) -> tuple[str, str]:
    match = None
    try:
        parsed = urlsplit(value)
        match = _TARGET.fullmatch(parsed.path)
        valid = (parsed.scheme == "https" and parsed.netloc == "my.tonies.com"
                 and not parsed.fragment and parsed.query in ("", "withUpload=true") and match)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError("Invalid Tonies target URL; select a Creative-Tonie editor on my.tonies.com")
    return match.group(1), match.group(2)


def editor_url(household_id: str, tonie_id: str) -> str:
    value = f"https://my.tonies.com/creative-tonies/{household_id}/{tonie_id}/edit?withUpload=true"
    parse_target_url(value)
    return value


def _processing(value) -> bool:
    if isinstance(value, str) and value.lower() in {"error", "failed", "failure"}:
        raise RuntimeError("Tonies audio processing failed")
    return bool(value)


def _chapters(value) -> list[dict]:
    if not isinstance(value, list):
        raise RuntimeError("Invalid Tonies chapter response")
    identities = set()
    for chapter in value:
        if not isinstance(chapter, dict) or any(not isinstance(chapter.get(key), str) for key in ("id", "title", "file", "type")):
            raise RuntimeError("Invalid Tonies chapter response")
        if not chapter["id"] or chapter["id"] in identities:
            raise RuntimeError("Invalid or duplicate Tonies chapter identity")
        identities.add(chapter["id"])
        _number(chapter.get("seconds"), "chapter duration")
        _processing(chapter.get("transcoding"))
    return value


def _writable(chapters: list[dict]) -> list[dict]:
    return [{key: chapter[key] for key in ("id", "title", "file", "seconds", "type")} for chapter in chapters]


def _matches_chapters(actual, expected, processing_ids):
    comparable, expected = _writable(actual), _writable(expected)
    for chapter, original in zip(comparable, expected):
        if chapter["id"] == original["id"] and chapter["id"] in processing_ids:
            # Only established processing identities may acquire a new file/duration.
            chapter["file"], chapter["seconds"] = original["file"], original["seconds"]
    return comparable == expected


def _content(target: dict, config: dict) -> dict:
    total = _number(config.get("maxSeconds"), "capacity")
    free = _number(target.get("secondsRemaining"), "remaining capacity")
    chapters = []
    for index, chapter in enumerate(_chapters(target.get("chapters"))):
        seconds = _number(chapter["seconds"], "chapter duration")
        rounded = int(seconds)
        token = _TRACK.search(chapter["title"])
        chapters.append({"index": index, "chapter_id": chapter["id"], "title": chapter["title"],
                         "display_title": _TRACK.sub("", chapter["title"]).strip() or chapter["title"],
                         "app_track_token": token.group(1).lower() if token else "",
                         "duration": f"{rounded // 3600:02}:{rounded // 60 % 60:02}:{rounded % 60:02}",
                         "seconds": seconds,
                         "processing": _processing(chapter.get("transcoding"))})
    return {"free_minutes": int(free // 60), "total_minutes": int(total // 60),
            "used_minutes": math.ceil(max(total - free, 0) / 60),
            "free_seconds": free, "total_seconds": total,
            "processing": _processing(target.get("transcoding")) or any(c["processing"] for c in chapters),
            "chapters": chapters}


class ToniesContent(ToniesSession):
    def _query(self, ids=None):
        variables = {"householdId": ids[0], "creativeTonieId": ids[1]} if ids else {}
        result = self._api("POST", "/graphql", {"query": QUERY, "variables": variables}, read=True)
        if result.get("errors"):
            raise RuntimeError("Tonies GraphQL request failed")
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("config"), dict) or not isinstance(data.get("households"), list):
            raise RuntimeError("Invalid Tonies GraphQL response")
        for household in data["households"]:
            if (not isinstance(household, dict) or not isinstance(household.get("id"), str)
                    or not isinstance(household.get("creativeTonies"), list)
                    or any(not isinstance(target, dict) or not isinstance(target.get("id"), str)
                           for target in household["creativeTonies"])):
                raise RuntimeError("Invalid Tonies GraphQL response")
        return data

    def _snapshot(self, ids):
        data = self._query(ids)
        targets = [target for house in data["households"] if house.get("id") == ids[0]
                   for target in house.get("creativeTonies", []) if target.get("id") == ids[1]]
        if len(targets) != 1:
            raise RuntimeError("Tonies target was not uniquely found in this account")
        _content(targets[0], data["config"])
        return targets[0], data["config"]

    def get_content(self, target_url):
        target, config = self._snapshot(parse_target_url(target_url))
        return _content(target, config)

    def list_targets(self):
        data = self._query()
        result = []
        seen = set()
        for house in data["households"]:
            for target in house.get("creativeTonies", []):
                url = editor_url(house.get("id"), target.get("id"))
                if url in seen:
                    raise RuntimeError("Duplicate Tonies target identity")
                seen.add(url)
                result.append({"name": target.get("name") or "Creative-Tonie", "image": target.get("imageUrl") or "", "edit_url": url})
        return result

    def _readback(self, ids, expected, *, processing_ids, with_snapshot=False):
        deadline = time.monotonic() + self._poll_timeout
        for _ in range(40):
            target, config = self._snapshot(ids)
            if _matches_chapters(target["chapters"], expected, processing_ids):
                content = _content(target, config)
                return (content, copy.deepcopy(target["chapters"])) if with_snapshot else content
            if time.monotonic() >= deadline:
                break
            time.sleep(min(self._poll_interval, max(0, deadline - time.monotonic())))
        raise RuntimeError("Saved Tonies content was not confirmed; refresh before another action")
