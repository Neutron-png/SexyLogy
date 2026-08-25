"""
Export layer: streams job results out to CSV / JSON / JSONL / XLSX.

Takes an iterator of result dicts (see Database.iter_all_results) rather
than a materialized list, so exporting a 200k-row job doesn't require
holding it all in RAM at once (spec section 29, "performance").
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

ProgressCB = Optional[Callable[[int], None]]  # called with rows-written-so-far


def _rows(results: Iterable[dict]) -> Iterator[dict]:
    """Each stored result row is {id, job_id, source_url, data_json, scraped_at}.
    Flatten it to {**data, source_url, scraped_at} for export."""
    for r in results:
        data = json.loads(r["data_json"]) if isinstance(r.get("data_json"), str) else dict(r.get("data", {}))
        flat = dict(data)
        flat["source_url"] = r.get("source_url")
        flat["scraped_at"] = r.get("scraped_at")
        yield flat


def export_csv(results: Iterable[dict], dest: str | Path, on_progress: ProgressCB = None) -> int:
    dest = Path(dest)
    rows = list(_rows(results))
    fieldnames = _collect_fieldnames(rows)
    count = 0
    with dest.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _stringify(v) for k, v in row.items()})
            count += 1
            if on_progress and count % 100 == 0:
                on_progress(count)
    if on_progress:
        on_progress(count)
    return count


def export_json(results: Iterable[dict], dest: str | Path, on_progress: ProgressCB = None) -> int:
    dest = Path(dest)
    rows = list(_rows(results))
    with dest.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    if on_progress:
        on_progress(len(rows))
    return len(rows)


def export_jsonl(results: Iterable[dict], dest: str | Path, on_progress: ProgressCB = None) -> int:
    dest = Path(dest)
    count = 0
    with dest.open("w", encoding="utf-8") as f:
        for row in _rows(results):
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
            count += 1
            if on_progress and count % 100 == 0:
                on_progress(count)
    if on_progress:
        on_progress(count)
    return count


def export_xlsx(results: Iterable[dict], dest: str | Path, on_progress: ProgressCB = None) -> int:
    from openpyxl import Workbook

    rows = list(_rows(results))
    fieldnames = _collect_fieldnames(rows)

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Results")
    ws.append(fieldnames)
    count = 0
    for row in rows:
        ws.append([_stringify(row.get(k)) for k in fieldnames])
        count += 1
        if on_progress and count % 100 == 0:
            on_progress(count)
    wb.save(str(dest))
    if on_progress:
        on_progress(count)
    return count


EXPORTERS = {
    "csv": export_csv,
    "json": export_json,
    "jsonl": export_jsonl,
    "xlsx": export_xlsx,
}


def export(fmt: str, results: Iterable[dict], dest: str | Path, on_progress: ProgressCB = None) -> int:
    fmt = fmt.lower().lstrip(".")
    if fmt not in EXPORTERS:
        raise ValueError(f"صيغة تصدير غير مدعومة: {fmt}. المتاح: {', '.join(EXPORTERS)}")
    return EXPORTERS[fmt](results, dest, on_progress)


def _collect_fieldnames(rows: list[dict]) -> list[str]:
    names: list[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                names.append(key)
    return names


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
