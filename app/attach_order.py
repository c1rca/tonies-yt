"""Attach several parallel downloads in a fixed order."""
import threading

TURN_TIMEOUT_SEC = 1800


class AttachOrder:
    """Each job waits for the one before it to hand its upload over (or fail) first.

    Downloads still run in parallel; only the hand-off to the Tonie's action queue,
    which decides the final track order, is serialised.
    """

    def __init__(self, job_ids: list[str]):
        self._handed_over = {job_id: threading.Event() for job_id in job_ids}
        self._previous = dict(zip(job_ids[1:], job_ids[:-1]))

    def wait_turn(self, job_id: str) -> None:
        previous = self._previous.get(job_id)
        if previous is not None:
            # Best effort after the timeout: a stuck predecessor must not block the rest forever.
            self._handed_over[previous].wait(TURN_TIMEOUT_SEC)

    def done(self, job_id: str) -> None:
        self._handed_over[job_id].set()
