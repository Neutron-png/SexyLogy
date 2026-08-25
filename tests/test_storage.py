"""
Note: uses a plain contextmanager helper instead of a pytest fixture so
these tests can run under either `pytest` or stdlib `unittest`/a manual
runner (this sandbox has no network access to install pytest - see
tests/run_tests.py). If pytest is available in your environment, feel
free to switch this back to a fixture.
"""
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from app.core.storage.db import Database


@contextmanager
def temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(Path(tmp) / "test.db")
        try:
            yield database
        finally:
            database.close()


def test_create_and_list_project():
    with temp_db() as db:
        pid = db.create_project("Test Project", {"target": {"start_urls": ["https://x.com"]}})
        projects = db.list_projects()
        assert len(projects) == 1
        assert projects[0]["name"] == "Test Project"
        assert projects[0]["id"] == pid


def test_job_lifecycle_and_results():
    with temp_db() as db:
        pid = db.create_project("P", {})
        job_id = db.create_job(pid, pages_total=5)
        job = db.get_job(job_id)
        assert job["status"] == "running"

        for i in range(3):
            db.add_result(job_id, f"https://x.com/{i}", {"name": f"item-{i}"})

        assert db.count_results(job_id) == 3
        db.update_job_progress(job_id, pages_done=3, records_ok=3, records_failed=0)
        db.finish_job(job_id, "completed")

        job = db.get_job(job_id)
        assert job["status"] == "completed"
        assert job["records_ok"] == 3
        assert job["finished_at"] is not None


def test_windowed_results_pagination():
    with temp_db() as db:
        pid = db.create_project("P", {})
        job_id = db.create_job(pid)
        for i in range(25):
            db.add_result(job_id, "https://x.com", {"n": i})

        page1 = db.page_results(job_id, offset=0, limit=10)
        page2 = db.page_results(job_id, offset=10, limit=10)
        page3 = db.page_results(job_id, offset=20, limit=10)
        assert len(page1) == 10
        assert len(page2) == 10
        assert len(page3) == 5

        all_via_iter = list(db.iter_all_results(job_id, batch_size=7))
        assert len(all_via_iter) == 25


def test_settings_roundtrip():
    with temp_db() as db:
        db.set_setting("default_fetcher", "stealth")
        assert db.get_setting("default_fetcher") == "stealth"
        assert db.get_setting("does_not_exist", "fallback") == "fallback"


def test_delete_project_cascades_nothing_breaks():
    with temp_db() as db:
        pid = db.create_project("P", {})
        db.delete_project(pid)
        assert db.list_projects() == []


def test_db_usable_from_a_background_thread():
    """Regression test for the bug that made Stop/progress/log all appear
    broken at once: the Database was opened with sqlite3's default
    check_same_thread=True on the GUI thread, then ScrapeJobWorker (which
    runs on a separate QThread) called self.db.add_log(...) as the very
    first thing in run() - outside any try/except - and that raised
    sqlite3.ProgrammingError immediately, silently killing the worker
    thread before it ever reached the code that checks _stop_requested or
    emits progress. This test reproduces the shape of that access
    (create the Database on this/"main" thread, use it from a different
    thread) with plain `threading` instead of Qt, since PySide6 isn't
    installable in this sandbox - the fix (check_same_thread=False + an
    RLock in Database.cursor()) must make this thread-safe."""
    with temp_db() as db:
        pid = db.create_project("P", {})
        job_id = db.create_job(pid, pages_total=1)
        errors = []

        def worker():
            try:
                db.add_log(job_id, "INFO", "from background thread")
                db.add_result(job_id, "https://example.com", {"a": 1})
                db.update_job_progress(job_id, 1, 1, 0)
                db.finish_job(job_id, "completed")
            except Exception as e:  # pragma: no cover - the assertion below is what matters
                errors.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)

        assert not errors, f"Database call from a background thread raised: {errors}"
        logs = db.list_logs(job_id)
        assert any("from background thread" in l["message"] for l in logs)
        job = db.get_job(job_id)
        assert job["status"] == "completed"
