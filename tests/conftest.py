import pytest
from fastapi.testclient import TestClient

from app.payhawk.client import PayhawkClient
from app.payhawk.mock import store
from app.web import app

BASE = "/mock-payhawk/api/v3/accounts/demo_account"


@pytest.fixture
def http():
    store.reset()
    with TestClient(app, base_url="http://testserver", headers={"X-Payhawk-ApiKey": "demo-key"}) as c:
        yield c


@pytest.fixture
def client(http):
    return PayhawkClient(base_url="http://testserver" + BASE, http=http)


@pytest.fixture(autouse=True)
def rules_dir(tmp_path, monkeypatch):  # never write the real data/ dir
    monkeypatch.setattr("app.skills.OVERRIDE_ROOT", tmp_path / "skills")
