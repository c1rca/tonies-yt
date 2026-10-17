"""Offline fake of the observed my-tonies OAuth/v2 contract, shared by the API tests."""
import copy
import json
from urllib.parse import parse_qs

import httpx

HOUSEHOLD = "11111111-2222-4333-8444-555555555555"
TONIE = "aBcD0123456789EF"
URL = f"https://my.tonies.com/creative-tonies/{HOUSEHOLD}/{TONIE}/edit?withUpload=true"


def chapter(identity, title="Same", seconds=12):
    return {"id": identity, "title": title, "file": f"file-{identity}", "seconds": seconds, "type": "file", "transcoding": False}


class Cloud:
    def __init__(self):
        self.chapters = [chapter("one"), chapter("two")]
        self.live = False
        self.processing = False
        self.calls = []
        self.patches = []
        self.tokens = []
        self.storage = []
        self.graphql_error = False
        self.patch_timeout = False
        self.apply_patch = True
        self.concurrent_change = False
        self.reads = 0

    def snapshot(self):
        return {"data": {"config": {"maxSeconds": 5400, "maxChapters": 99, "maxBytes": 10000000, "accepts": ["audio/mpeg", "audio/wav"]}, "households": [{"id": HOUSEHOLD, "name": "Synthetic household", "creativeTonies": [{"id": TONIE, "name": "Blue", "imageUrl": "https://images.example.invalid/blue.png", "live": self.live, "transcoding": self.processing, "secondsRemaining": 5376, "chapters": copy.deepcopy(self.chapters)}]}]}}

    def __call__(self, request):
        self.calls.append(request)
        if request.url.host == "login.tonies.com":
            data = parse_qs(request.content.decode())
            self.tokens.append(data)
            assert request.url.path == "/auth/realms/tonies/protocol/openid-connect/token"
            assert data["client_id"] == ["my-tonies"]
            return httpx.Response(200, json={"access_token": "synthetic-access", "refresh_token": f"synthetic-refresh-{len(self.tokens)}", "expires_in": 300})
        if request.url.host == "synthetic.s3.amazonaws.com":
            assert "authorization" not in request.headers
            self.storage.append(request)
            return httpx.Response(204)
        assert request.url.host == "api.prod.tcs.toys"
        assert request.headers["authorization"] == "Bearer synthetic-access"
        if request.url.path == "/v2/graphql":
            self.reads += 1
            body = json.loads(request.content)
            assert "config" in body["query"]
            if body.get("variables", {}).get("householdId"):
                assert body["variables"] == {"householdId": HOUSEHOLD, "creativeTonieId": TONIE}
            if self.concurrent_change and self.reads == 2:
                self.chapters.append(chapter("foreign"))
            if self.graphql_error:
                return httpx.Response(200, json={"data": self.snapshot()["data"], "errors": [{"message": "private diagnostic"}]})
            return httpx.Response(200, json=self.snapshot())
        if request.url.path == "/v2/file":
            assert json.loads(request.content) == {"headers": {}}
            return httpx.Response(200, json={"fileId": "staging", "request": {"url": "https://synthetic.s3.amazonaws.com/", "fields": {"key": "signed-key", "policy": "synthetic-policy", "x-amz-signature": "synthetic-signature"}}})
        assert request.method == "PATCH"
        assert request.url.path == f"/v2/households/{HOUSEHOLD}/creativetonies/{TONIE}"
        payload = json.loads(request.content)
        self.patches.append(payload)
        result = copy.deepcopy(payload["chapters"])
        for item in result:
            if item["id"] == "staging":
                item["id"] = "permanent"
                item["file"] = "transcoded-reference"
                item["transcoding"] = True
        if self.apply_patch:
            self.chapters = copy.deepcopy(result)
        if self.patch_timeout:
            raise httpx.ReadTimeout("sensitive-request-query-token", request=request)
        return httpx.Response(200, json={"id": TONIE, "chapters": result, "transcoding": self.processing})
