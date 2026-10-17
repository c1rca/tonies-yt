#!/usr/bin/env python3
"""Run Tonies-YT against an in-memory fake Tonies cloud, for trying UI changes without an account.

    .venv/bin/python scripts/dev/fake_cloud_app.py [--port 8099] [--latency 0.25]

Uses a throwaway data directory with its own vault and a few generated MP3s. Every
request to the "cloud" waits ``--latency`` seconds, so batching and queueing are
visible. New uploads finish "processing" a few seconds after they are attached.
The sign-in session token is written to <data dir>/session.txt (set it as the
``tonies_yt_session`` cookie) and the app password to <data dir>/password.txt.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--latency", type=float, default=0.25, help="seconds added to every cloud request")
    parser.add_argument("--tracks", type=int, default=8)
    args = parser.parse_args()

    data = Path(tempfile.mkdtemp(prefix="tonies-fake-cloud-"))
    os.environ["DATA_DIR"] = str(data)
    os.environ["LOG_FILE"] = str(data / "logs" / "app.log")
    os.environ.pop("TONIES_EMAIL", None)
    os.environ.pop("TONIES_PASSWORD", None)
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

    import httpx
    import uvicorn
    from tonies_fake import Cloud, chapter

    from app import auth, credentials, main as app_main, uploader
    from app.tonies_api import ToniesAPI

    cloud = Cloud()
    cloud.chapters = [chapter(f"track-{i}", f"Song {i + 1}", 60 + i) for i in range(args.tracks)]
    lock = threading.Lock()
    finish_at: dict[str, float] = {}

    def transport(request):
        time.sleep(args.latency)
        with lock:
            now = time.monotonic()
            for item in cloud.chapters:
                if item.get("transcoding") and finish_at.setdefault(item["id"], now + 4) <= now:
                    item["transcoding"] = False
            return cloud(request)

    api = ToniesAPI("fake@example.invalid", "fake-password",
                    client=httpx.Client(transport=httpx.MockTransport(transport)))
    uploader._get_api_client = lambda *_args, **_kwargs: api

    password = secrets.token_urlsafe(12)
    credentials.initialize_vault("dev", password, "fake@example.invalid", "fake-password")
    (data / "password.txt").write_text(password)
    downloads = data / "downloads"
    downloads.mkdir(exist_ok=True)
    for i, name in enumerate(["Lullaby", "Ducks", "Rainbow", "Train", "Moon"]):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + 80 * i}:duration=3",
                        "-ac", "2", "-b:a", "64k", str(downloads / f"{name}.mp3")], check=True)

    def mint_session():
        # The server's startup doesn't touch sessions; mint one once it is running.
        time.sleep(1.5)
        (data / "session.txt").write_text(auth.create_session())
        print(f"fake cloud app on http://{args.host}:{args.port}  data={data}", flush=True)

    threading.Thread(target=mint_session, daemon=True).start()
    uvicorn.run(app_main.app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
