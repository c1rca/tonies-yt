import httpx
import pytest

from tonies_fake import Cloud


@pytest.fixture
def cloud_api():
    from app.tonies_api import ToniesAPI
    cloud = Cloud()
    api = ToniesAPI("synthetic@example.invalid", "synthetic-password", client=httpx.Client(transport=httpx.MockTransport(cloud)), poll_timeout=0.01, poll_interval=0)
    yield cloud, api
    api.close()
