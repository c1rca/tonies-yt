"""Attach several parallel downloads in a fixed order."""
import threading

# Shorter than the jobs watchdog limit for "waiting_upload", so a stuck turn fails here, cleanly.
TURN_TIMEOUT_SEC = 1500


class AttachOrder:
    """Each job waits for the one before it to hand its upload over (or fail) first.

    Downloads still run in parallel; only the hand-off to the Tonie's action queue,
    which decides the final track order, is serialised. Jobs must be started in
    this order on a first-in-first-out pool, so a predecessor is always running
    or finished while its successor waits.
    """

    def __init__(self, job_ids: list[str]):
        self._handed_over = {job_id: threading.Event() for job_id in job_ids}
        self._previous = dict(zip(job_ids[1:], job_ids[:-1]))

    @property
    def order(self) -> list[str]:
        return list(self._handed_over)

    def wait_turn(self, job_id: str) -> bool:
        """True when it's this job's turn; False on timeout (never hand over out of order)."""
        previous = self._previous.get(job_id)
        return previous is None or self._handed_over[previous].wait(TURN_TIMEOUT_SEC)

    def done(self, job_id: str) -> None:
        self._handed_over[job_id].set()
