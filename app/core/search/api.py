"""
LOGY Search local HTTP API - OPTIONAL debug/playground surface.

In-app code calls SearchService directly in-process (faster, no socket
hop). This FastAPI app exists so the search layer can be driven and
inspected from OUTSIDE (curl, tests, future tooling) - exactly like a
SERP API, but localhost-only.

Security posture:
- bound to 127.0.0.1 by the runners (tools/run_search_api.py, UI toggle)
- auth: if LOGY_SEARCH_API_TOKEN env is set, every request must carry
  `X-Logy-Token: <token>`; unset = localhost-trusted (desktop app)
- no URL-fetching endpoint at all -> no SSRF surface
- upstream provider config is never echoed in responses
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from app.core.search.schema import SearchRequest, SearchResponse


def create_app(service) -> FastAPI:
    app = FastAPI(title="LOGY Search API", version="0.1.0", docs_url="/docs")
    required_token = os.environ.get("LOGY_SEARCH_API_TOKEN", "")

    def _check_token(token: str):
        if required_token and token != required_token:
            raise HTTPException(status_code=401, detail="invalid token")

    @app.post("/api/v1/search")
    async def search(request: Request, body: dict, x_logy_token: str = Header(default="")) -> dict:
        _check_token(x_logy_token)
        try:
            req = SearchRequest(**body)
        except ValidationError as e:
            # pydantic error dicts can carry non-serializable ctx objects -
            # flatten to strings so the 422 response is always encodable
            raise HTTPException(status_code=422, detail=[
                {"field": ".".join(str(x) for x in err.get("loc", [])),
                 "msg": err.get("msg", ""), "type": err.get("type", "")}
                for err in e.errors()
            ])
        try:
            resp = await service.search_async(req)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
        return resp.model_dump()

    @app.get("/health")
    async def health(x_logy_token: str = Header(default="")) -> dict:
        _check_token(x_logy_token)
        return {"ok": True, "service": "logy-search"}

    @app.get("/metrics")
    async def metrics(x_logy_token: str = Header(default="")) -> dict:
        _check_token(x_logy_token)
        return service.health_report()

    return app


__all__ = ["create_app"]
