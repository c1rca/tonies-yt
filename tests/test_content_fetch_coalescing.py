import threading
import time

from app import uploader


def test_concurrent_refreshes_share_one_fresh_remote_fetch(monkeypatch):
    calls = []
    started = threading.Event()
    release = threading.Event()

    def remote_fetch(url):
        calls.append(url)
        started.set()
        assert release.wait(1)
        return {"chapters": [{"title": "fresh"}], "source": "remote"}

    monkeypatch.setattr(uploader, "_get_tonies_content_uncached", remote_fetch)
    uploader._content_fetches.clear()
    results = []
    threads = [threading.Thread(target=lambda: results.append(uploader.get_tonies_content("target"))) for _ in range(3)]
    for thread in threads:
        thread.start()
    assert started.wait(1)
    time.sleep(0.02)
    release.set()
    for thread in threads:
        thread.join(1)

    assert calls == ["target"]
    assert results == [{"chapters": [{"title": "fresh"}], "source": "remote"}] * 3
    assert uploader._content_fetches == {}
