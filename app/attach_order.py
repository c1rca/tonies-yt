"""Attach several parallel downloads in a fixed order."""
import threading
import time

# Shorter than the jobs watchdog limit for "waiting_upload", so a stuck turn fails here, cleanly.
TURN_TIMEOUT_SEC = 1500


class AttachOrder:
    """Each job waits for every job before it to hand its upload over (or fail) first.

    Downloads still run in parallel; only the hand-off to the Tonie's action queue,
    which decides the final track order, is serialised. Jobs must be started in
    this order on a first-in-first-out pool, so a predecessor is always running
    or finished while its successor waits.
    """

    def __init__(self, job_ids: list[str]):
        self._handed_over = {job_id: threading.Event() for job_id in job_ids}
        # Every earlier job, not just the one before: a job that fails early marks itself
        # done at once, and must not let a later one overtake a still-running earlier one.
        self._earlier = {job_id: job_ids[:i] for i, job_id in enumerate(job_ids)}

    @property
    def order(self) -> list[str]:
        return list(self._handed_over)

    def wait_turn(self, job_id: str) -> bool:
        """True once every earlier job has handed over or failed; False on timeout
        (never hand over out of order)."""
        deadline = time.monotonic() + TURN_TIMEOUT_SEC
        for earlier in self._earlier.get(job_id, []):
            if not self._handed_over[earlier].wait(max(0.0, deadline - time.monotonic())):
                return False
        return True

    def done(self, job_id: str) -> None:
        self._handed_over[job_id].set()
