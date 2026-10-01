from __future__ import annotations
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

fastapi = pytest.importorskip("fastapi")


@pytest.fixture()
def service(tmp_path):
    from app.core.storage.db import Database
    from app.core.search.service import SearchService

    db = Database(tmp_path / "api.db")
    svc = SearchService(db, config={"searxng_base_url": ""})
    fake = _StubProvider()
    svc._providers = lambda request: [fake]
    svc._fake = fake
    yield svc
    db.close()


class _StubProvider:
    name = "stub"
    from app.core.search.schema import ProviderCapabilities
    capabilities = ProviderCapabilities(organic=True)

    def can_serve(self, request):
        return True

    async def search(self, request, client):
        from app.core.search.schema import OrganicResult, ResultBlocks
        return ResultBlocks(organic=[
            OrganicResult(position=1, title="Stub result", url="https://stub.example/x")
        ])


@pytest.fixture()
def client(service):
    from fastapi.testclient import TestClient
    from app.core.search.api import create_app

    return TestClient(create_app(service))


def test_search_endpoint_shape(client):
    r = client.post("/api/v1/search", json={
        "query": "luxury landscape lighting companies", "page": 1,
        "country": "us", "language": "en", "num_results": 20, "type": "search",
    })
    assert r.status_code == 200
    data = r.json()
    for key in ("request_id", "query", "page", "provider", "results", "meta"):
        assert key in data
    assert data["results"]["organic"][0]["url"] == "https://stub.example/x"
    assert data["meta"]["cached"] is False


def test_search_endpoint_cache_flag(client):
    body = {"query": "cache flag query"}
    r1 = client.post("/api/v1/search", json=body).json()
    r2 = client.post("/api/v1/search", json=body).json()
    assert r1["meta"]["cached"] is False
    assert r2["meta"]["cached"] is True


def test_validation_error_is_422(client):
    r = client.post("/api/v1/search", json={"query": "   "})
    assert r.status_code == 422


def test_health_and_metrics(client, service):
    assert client.get("/health").json()["ok"] is True
    client.post("/api/v1/search", json={"query": "m1"})
    m = client.get("/metrics").json()
    assert m["metrics"]["requests"] == 1
    assert any(p["provider"] == "stub" for p in m["providers"]) or m["providers"] == []


def test_auth_token(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.core.storage.db import Database
    from app.core.search.service import SearchService
    from app.core.search.api import create_app

    monkeypatch.setenv("LOGY_SEARCH_API_TOKEN", "s3cret")
    db = Database(tmp_path / "auth.db")
    svc = SearchService(db, config={"searxng_base_url": ""})
    svc._providers = lambda request: [_StubProvider()]
    c = TestClient(create_app(svc))
    assert c.post("/api/v1/search", json={"query": "q"}).status_code == 401
    assert c.post("/api/v1/search", json={"query": "q"},
                  headers={"X-Logy-Token": "wrong"}).status_code == 401
    assert c.post("/api/v1/search", json={"query": "q"},
                  headers={"X-Logy-Token": "s3cret"}).status_code == 200
    assert c.get("/metrics").status_code == 401
    db.close()
