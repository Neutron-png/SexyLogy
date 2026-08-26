# LOGY

A desktop GUI for web scraping and lead generation, built on top of the
[Scrapling](https://github.com/D4Vinci/Scrapling) scraping engine.

## What's implemented

This is a real, wired-up application - not a mockup. Every button that
appears in the UI is connected to real logic; nothing renders fake data.

| Layer | Status | Notes |
|---|---|---|
| App shell, sidebar nav, dark theme | Done | `app/ui/main_window.py`, `app/ui/theme.py` |
| New Scrape screen (Target / Extract / Options / Proxy) | Done | `app/ui/screens/new_scrape.py` |
| Field Builder (visual, no-code) | Done | `app/ui/widgets/field_builder.py` |
| Smart Extraction (NL -> fields) | Done, rule-based | `app/core/engine/nl_to_fields.py` - keyword matcher, not an LLM call. See note below. |
| JSON Schema mode | Done | validated before a job can start |
| Scrapling integration (Fetcher/DynamicFetcher/StealthyFetcher) | Done | `app/core/engine/scrapling_adapter.py` - the only file that imports `scrapling` |
| Background job manager (QThread, pause/resume/stop) | Done | `app/core/job_manager.py` |
| Live logs + progress + virtualized results table | Done | `app/ui/widgets/log_panel.py`, `app/ui/widgets/results_table.py` |
| Export (CSV/JSON/JSONL/XLSX), streamed | Done | `app/core/exports/exporter.py` |
| Projects / History / Templates / Settings / API Keys / Logs screens | Done | SQLite-backed, `app/core/storage/db.py` |
| Encrypted secret storage for proxy/API credentials | Done | `app/core/storage/secrets.py` (Fernet) |
| Page Preview / visual "click an element" Selector Assistant | **Not implemented yet** | needs an embedded browser widget (`QWebEngineView`); see "Next steps" |
| Spider-based multi-page crawling with Scrapling's own `Spider` class | **Partial** | current job manager does its own BFS crawl via `fetch_one` + `extract_links`; swapping in `scrapling.spiders.Spider` directly (with its native pause/resume-to-disk) is a follow-up, see below |
| PyInstaller packaging | Documented, not run | must be built on the target OS, see "Packaging" |

## Why the "Smart Extraction" mode isn't AI-backed by default

The spec asks for the AI layer to be isolated from the scraping engine
and *not required* for basic scraping to work. `nl_to_fields.py`
implements `generate_fields(description) -> list[ExtractionField]` as a
small keyword matcher, which is honest about what it does rather than
pretending to be an LLM. Swapping in a real LLM call later means
implementing the same function signature and pointing
`new_scrape.py -> _generate_smart_fields()` at it - nothing else changes.

## Important limitation of this build

This codebase was written and unit-tested in a network-isolated sandbox
that cannot reach PyPI or download Playwright/patchright browser
binaries. That means:

- The pure-Python core (`app/core/engine/extractor.py`,
  `app/utils/validation.py`, `app/core/storage/db.py`,
  `app/core/exports/exporter.py`) was actually executed and verified -
  run `python tests/run_tests.py` (no pytest required) and you'll see
  24/24 tests pass.
- The PySide6 UI and the real Scrapling calls were written against the
  documented APIs but **could not be launched or click-tested** in this
  environment, since `pip install PySide6` / `pip install scrapling` /
  `scrapling install` all require network access this sandbox doesn't
  have. `python -m py_compile` confirms every file is syntactically
  valid, but that is not the same as running it.

You should expect the first real run on your machine to surface a few
integration-level bugs (an off-by-one in a Qt layout, a Scrapling
keyword argument that changed between versions, etc.) - that's normal
for a from-scratch build validated this way, and this document tells you
exactly how to find and fix them quickly.

## Setup (on your own machine)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
scrapling install                # downloads the browser binaries Stealth/Dynamic fetchers need
python main.py
```

On first run LOGY creates a SQLite database under your OS's app-data
folder (`%APPDATA%/LOGY` on Windows, `~/.local/share/LOGY` on Linux) and
seeds the built-in templates. The sidebar's "Engine" indicator shows red
if `scrapling` failed to import - hover it for the reason.

## Running the tests

```bash
python tests/run_tests.py     # zero extra dependencies
# or, if you have pytest installed:
pytest
```

## Project structure

```
main.py                        entry point
app/
  ui/                           PySide6 screens/widgets/theme - no scraping logic here
    main_window.py, sidebar.py, theme.py
    screens/                    New Scrape, Dashboard, Projects, History, Templates, Settings, API Keys, Logs
    widgets/                    FieldBuilder, ResultsTableModel (virtualized), LogPanel
  core/
    models.py                   plain dataclasses, no Qt/Scrapling imports
    job_manager.py               QThread-based background job runner
    engine/
      scrapling_adapter.py       the ONLY file that imports `scrapling`
      extractor.py                applies ExtractionFields to a fetched page
      nl_to_fields.py              Smart Extraction keyword matcher
      builtin_templates.py         seeds Business Leads / Product Data / etc.
    storage/
      db.py                       SQLite: projects, jobs, results, logs, settings, templates
      secrets.py                   Fernet-encrypted credential storage
    exports/
      exporter.py                  streamed CSV/JSON/JSONL/XLSX export
  utils/
    validation.py                 URL/selector/JSON-schema validation
tests/                            24 passing tests for everything that doesn't need Qt/network
```

## Packaging into a desktop executable

PyInstaller must run **on the target OS** - build the Windows `.exe` on a
Windows machine, the macOS app on macOS, etc. This could not be produced
inside this sandbox (Linux container, no PyInstaller/PySide6 available).

```bash
pip install pyinstaller
pyinstaller --name LOGY --windowed --onedir main.py
```

Notes for a clean Windows build:
- `--windowed` suppresses the console window.
- Scrapling's browser binaries (downloaded by `scrapling install`) live
  outside the PyInstaller bundle by default - either instruct users to
  run `scrapling install` once after installing LOGY, or bundle the
  browser directory with `--add-data` and set `executable_path` /
  `cdp_url` accordingly in `scrapling_adapter.py`.
- Add an `.ico` built from the LOGY icon and pass `--icon logy.ico`.

## Known gaps vs. the full spec, and suggested next steps

1. **Page Preview / click-to-select Selector Assistant (spec section 9).**
   Needs `QWebEngineView` (from `PySide6-Addons`/`PyQt6-WebEngine`) to
   render the target page, a JS injection to highlight the hovered
   element and report its computed CSS/XPath back to Python via
   `QWebChannel`, and a "Use this element" button that writes into the
   Field Builder. This is a self-contained addition to
   `app/ui/screens/new_scrape.py` - it doesn't touch the engine layer.
2. **Native Scrapling `Spider` for multi-page crawls.** The current job
   manager does its own breadth-first crawl loop calling
   `scrapling_adapter.fetch_one()` per page. Scrapling's own `Spider`
   class (see `scrapling.spiders.Spider`) natively supports
   `concurrent_requests`, `response.follow()`, and disk-backed
   pause/resume (`crawldir=`). Migrating `ScrapeJobWorker.run()` to drive
   a `Spider` subclass instead of a manual queue would pick up that
   pause/resume-across-restarts behavior for free - worth doing once the
   manual loop above is confirmed working end-to-end.
3. **First-run dependency wizard (spec section 23).** Right now a missing
   Scrapling install just shows a red status dot in the sidebar and a
   blocking error dialog on Start. A proper first-run screen (checklist
   UI, "Install Required Components" button that shells out to
   `pip install scrapling && scrapling install` with a progress log)
   would match the spec more closely - `app/ui/screens/settings.py`'s
   Browser tab has the status check already; it just needs the install
   flow wired to a QProcess.
4. **Proxy rotation across a list.** `scrapling_adapter._proxy_kwarg()`
   currently always returns `proxies[0]`; true rotation (round-robin or
   random per request) needs a small stateful picker in
   `ScrapeJobWorker` that advances an index each time `fetch_one()` is
   called with `proxy.mode == "list"/"rotating"`.
5. **Run this on a machine with network access** and fix whatever
   surfaces on the first real `python main.py` - see the limitation note
   above.
## **NOTE**
انا افجر واحد في بلدكوووووووووووووو
