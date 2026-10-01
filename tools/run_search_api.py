from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

"""Standalone runner for the local LOGY Search API (debug/playground).

Usage:
  python tools/run_search_api.py            # 127.0.0.1:8777, logy.db
  LOGY_SEARCH_API_TOKEN=secret python ...   # require X-Logy-Token header
  LOGY_SEARCH_DB=other.db python ...        # different database file

Hard-binds to 127.0.0.1 - never exposed to the network.
"""
import uvicorn

from app.core.storage.db import Database
from app.core.search.service import SearchService
from app.core.search.api import create_app

db = Database(os.environ.get("LOGY_SEARCH_DB", "logy.db"))
service = SearchService(db)
app = create_app(service)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("LOGY_SEARCH_PORT", "8777")),
                log_level="warning")
