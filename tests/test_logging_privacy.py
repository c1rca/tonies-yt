import logging
from app import logger as app_logging


def test_http_transport_debug_logs_cannot_expose_signed_urls(monkeypatch, tmp_path):
    monkeypatch.setattr(app_logging.settings, 'log_file', tmp_path / 'app.log')
    monkeypatch.setattr(app_logging.settings, 'log_level', 'DEBUG')
    app_logging.setup_logging()
    assert logging.getLogger('httpx').getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger('httpcore').getEffectiveLevel() >= logging.WARNING
