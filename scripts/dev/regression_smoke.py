#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from urllib import error, parse, request

KNOWN_BAD = [
    "using playwright sync api inside the asyncio loop",
    "cannot switch to a different thread",
]


def api(base: str, path: str, method: str = "GET", data: dict | None = None, timeout: int = 30):
    url = base.rstrip("/") + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", errors="replace")
            if not txt:
                return {}
            return json.loads(txt)
    except error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {path}: {msg[:300]}")


def poll_job(base: str, job_id: str, timeout_s: int = 600):
    start = time.time()
    while time.time() - start < timeout_s:
        jobs = api(base, "/api/jobs")
        j = next((x for x in jobs if x.get("id") == job_id), None)
        if not j:
            time.sleep(1)
            continue
        s = str(j.get("status", ""))
        if s in {"awaiting_selection", "done", "failed"}:
            return j
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for job {job_id}")


def grep_bad_logs(container: str, tail: int = 400) -> list[str]:
    try:
        out = subprocess.check_output([
            "docker", "logs", "--tail", str(tail), container
        ], stderr=subprocess.STDOUT, text=True)
    except Exception as e:
        return [f"log-read-failed: {e}"]
    bad = []
    low = out.lower()
    for k in KNOWN_BAD:
        if k in low:
            bad.append(k)
    return bad


def pick_short_candidate_index(job: dict, min_seconds: int = 90, max_seconds: int = 210) -> int:
    cands = job.get("candidates") or []
    best = -1
    for i, c in enumerate(cands):
        try:
            d = int(c.get("duration") or 0)
        except Exception:
            d = 0
        if min_seconds <= d <= max_seconds:
            return i
        if best < 0 and d > 0 and d <= max_seconds + 60:
            best = i
    return best if best >= 0 else 0


def main():
    p = argparse.ArgumentParser(description="Tonies-YT regression smoke for upload/thread-loop races")
    p.add_argument("--base", default=os.environ.get("TONIES_BASE", "http://localhost:8095"))
    p.add_argument("--container", default=os.environ.get("TONIES_CONTAINER", "tonies-yt-test-clean"))
    p.add_argument("--query", default="five little ducks")
    p.add_argument("--poll-seconds", type=int, default=240)
    p.add_argument("--probes", type=int, default=4, help="How many read-probe cycles to run during upload")
    p.add_argument("--probe-interval", type=float, default=3.0, help="Seconds between probe cycles")
    p.add_argument("--min-seconds", type=int, default=90, help="Preferred minimum candidate duration")
    p.add_argument("--max-seconds", type=int, default=210, help="Preferred maximum candidate duration")
    args = p.parse_args()

    base = args.base

    print(f"[1/8] health: {base}")
    h = api(base, "/api/health")
    if h.get("status") != "ok":
        raise RuntimeError(f"health not ok: {h}")

    print("[2/8] setup status")
    st = api(base, "/api/setup/status")
    if not st.get("configured"):
        raise RuntimeError("app not configured yet")
    if not st.get("unlocked"):
        raise RuntimeError("app is locked; unlock in UI first, then rerun")

    print("[3/8] fetch Creative Tonies")
    tonies = api(base, "/api/creative-tonies")
    if not isinstance(tonies, list) or not tonies:
        raise RuntimeError(f"no creative tonies returned: {tonies}")
    target_url = tonies[0].get("edit_url")
    if not target_url:
        raise RuntimeError("first creative tonies item has no edit_url")

    print("[4/8] search youtube")
    sr = api(base, "/api/chat", method="POST", data={"message": args.query, "search_limit": 5}, timeout=60)
    job_id = sr.get("job_id")
    if not job_id:
        raise RuntimeError(f"missing job_id from /api/chat: {sr}")

    print("[5/8] wait awaiting_selection")
    j = poll_job(base, job_id, timeout_s=args.poll_seconds)
    if j.get("status") != "awaiting_selection":
        raise RuntimeError(f"expected awaiting_selection, got {j.get('status')}")

    pick_idx = pick_short_candidate_index(j, min_seconds=args.min_seconds, max_seconds=args.max_seconds)
    picked = (j.get("candidates") or [{}])[pick_idx] if (j.get("candidates") or []) else {}
    picked_dur = int(picked.get("duration") or 0)
    print(f"[6/8] select candidate #{pick_idx} (duration={picked_dur}s) + race probes")
    api(base, f"/api/jobs/{job_id}/select", method="POST", data={"index": pick_idx, "target_url": target_url}, timeout=180)

    # During upload pipeline, run only a small number of read probes (gentle by default).
    probes = max(1, min(int(args.probes), 12))
    for _ in range(probes):
        try:
            api(base, "/api/creative-tonies", timeout=20)
        except Exception:
            pass
        try:
            api(base, "/api/tonies-content", method="POST", data={"target_url": target_url}, timeout=20)
        except Exception:
            pass
        jobs = api(base, "/api/jobs")
        cur = next((x for x in jobs if x.get("id") == job_id), None)
        if cur and str(cur.get("status")) in {"done", "failed"}:
            break
        time.sleep(max(0.5, float(args.probe_interval)))

    print("[7/8] await final job status")
    final = poll_job(base, job_id, timeout_s=args.poll_seconds)
    status = str(final.get("status"))
    if status != "done":
        raise RuntimeError(f"job did not complete: status={status} error={final.get('error')}")

    print("[8/8] final tonies content refresh + log scan")
    api(base, "/api/tonies-content", method="POST", data={"target_url": target_url}, timeout=30)
    bad = grep_bad_logs(args.container, tail=500)

    print("\n=== RESULT ===")
    print(f"job_id: {job_id}")
    print("status: done")
    if bad:
        print("BAD_LOG_PATTERNS_FOUND:")
        for b in bad:
            print(f" - {b}")
        sys.exit(2)
    print("No known Playwright thread/async-loop error patterns found.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
