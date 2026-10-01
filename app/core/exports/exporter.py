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
        # corrupted data_json (crash mid-write / manual edit): degrade to
        # an empty record so the export still completes (audit QA BUG-005)
        try:
            data = json.loads(r["data_json"]) if isinstance(r.get("data_json"), str) else dict(r.get("data", {}))
        except ValueError:
            data = {}
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


# ---------------------------------------------------------------------------
# Odoo CRM Lead import format - matches the exact column layout of Odoo's own
# "crm.lead" import template (the file a user downloads from Settings ->
# Import Records -> "crm_lead_1.xls" inside Odoo, or CRM -> Leads -> Import ->
# "Download Import Template"), including its "Import FAQ" sheet, so a file
# LOGY exports can be dropped straight into that same Odoo import screen with
# no manual re-mapping. See ODOO_COLUMNS below for the exact header order.
# ---------------------------------------------------------------------------
# The 16 columns of Odoo's OWN stock crm.lead import template, unchanged
# on every install. "Channel" used to be hardcoded as a 17th column here;
# it's now just the default entry of DEFAULT_ODOO_EXTRA_FIELDS below, and
# ODOO_COLUMNS is kept (same 17 values, same order) purely so existing
# callers/tests that import this constant directly keep working.
ODOO_STANDARD_COLUMNS = [
    "External ID", "Name", "Company Name", "Contact Name", "Email",
    "Job Position", "Phone", "Mobile", "Street", "Street2", "City",
    "State", "Zip", "Country", "Website", "Notes",
]

# "Channel" isn't part of Odoo's own stock crm.lead import template -
# it's a REQUIRED field only on THIS user's own Odoo instance (a custom
# field their install added, not something LOGY can know the valid
# values for by guessing - "مينفعش أخمن قيمة غلط هتبوظ الامبورت"). Every
# exported row gets the SAME fixed value from Settings -> "Odoo Export" ->
# "Channel value" (see SettingsScreen / db setting "odoo_channel_value"),
# defaulting to "Website" per the user's own answer, unless the scraped
# data already has a field literally called "channel" (checked first, in
# _lead_row_for_odoo() below, same as every other Odoo column).
DEFAULT_ODOO_CHANNEL_VALUE = "Website"

# ---------------------------------------------------------------------------
# "Review for any other required fields Odoo might demand depending on
# system config" - Odoo admins can mark ANY field on crm.lead as required
# per-install, via Studio or a plain field-level setting, so a hardcoded
# list can never cover every instance. "Channel" (above) was the first
# one this codebase hit in practice; it's really just ONE INSTANCE of a
# general shape: "a fixed value, applied to every exported row, for some
# column Odoo's stock template doesn't have and this install requires".
#
# EXTRA_ODOO_FIELDS generalizes that shape into a list instead of a single
# hardcoded "Channel" kwarg, configurable from Settings -> "Odoo Export"
# (db setting "odoo_extra_fields") without touching this file. Each entry:
#   {"column": "<Odoo column header>", "value": "<fallback value>",
#    "aliases": ("<scraped field name>", ...)}   # optional; defaults to
#                                                  # the lowercased column
# Precedence per row is identical to every other Odoo column: if the
# scraped lead already has a matching field (by alias), that value wins;
# otherwise the fixed "value" is used.
#
# Common OTHER fields that Odoo installs frequently require depending on
# configuration (Studio-added "required", a mandatory Sales Team on the
# CRM app, multi-company setups, UTM tracking made mandatory by a
# marketing module, etc.) - add any of these as extra rows in Settings if
# your own import error names them, using the exact value your Odoo
# instance expects:
#   Sales Team          (crm.team - e.g. "Website")
#   Salesperson          (res.users login/name)
#   Source                (utm.source - e.g. "Website")
#   Medium                (utm.medium - e.g. "Organic")
#   Campaign              (utm.campaign)
#   Tags                  (crm.tag, comma-separated for multiple)
#   Company               (res.company - required on multi-company installs)
#   Priority               (Low/Medium/High/Very High)
#   Expected Revenue        (numeric; some pipelines require a non-zero value)
# LOGY cannot guess any of these correctly (wrong values corrupt the
# import same as a missing one), so none are pre-filled - this is
# documentation for what to look for in Odoo's own import error message,
# not a default LOGY applies silently.
DEFAULT_ODOO_EXTRA_FIELDS: list[dict] = [
    {"column": "Channel", "value": DEFAULT_ODOO_CHANNEL_VALUE, "aliases": ("channel", "lead_channel", "source_channel")},
]

# Kept for backward compatibility: existing callers/tests import this
# fixed 17-column list directly. Same values, same order as before this
# file generalized "Channel" into DEFAULT_ODOO_EXTRA_FIELDS above.
ODOO_COLUMNS = ODOO_STANDARD_COLUMNS + [f["column"] for f in DEFAULT_ODOO_EXTRA_FIELDS]

# One or more scraped-field names (case-insensitive) that map to each Odoo
# column. Scraped field names come from whatever the user's own Field
# Builder / AI Auto-Extract called them, so this is a best-effort guess
# rather than a fixed schema - first matching key wins per column.
_ODOO_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "Name": ("name", "title", "lead_name", "opportunity"),
    "Company Name": ("company_name", "company", "business_name", "business"),
    "Contact Name": ("contact_name", "full_name", "owner_name", "contact"),
    "Email": ("email", "e-mail", "mail", "owner_email", "contact_email"),
    "Job Position": ("job_position", "position", "job_title", "title"),
    "Phone": ("phone", "phone_number", "telephone", "tel"),
    "Mobile": ("mobile", "owner_phone", "cell"),
    "Street": ("street", "address", "address1"),
    "Street2": ("street2", "address2"),
    "City": ("city",),
    "State": ("state", "region", "province"),
    "Zip": ("zip", "zipcode", "postal_code", "postcode"),
    "Country": ("country", "country_code"),
    "Website": ("website", "site", "url", "web", "homepage"),
    "Notes": ("notes", "description", "note", "comment", "comments"),
}


def _resolve_extra_fields(
    extra_fields: list[dict] | None, channel_value: str | None,
) -> list[dict]:
    """Reconciles the new generic `extra_fields` list with the older,
    single-purpose `channel_value` kwarg every existing caller/test still
    passes, so neither has to know about the other:

    - `extra_fields` given -> used as-is (the caller owns the whole list;
      this is the Settings -> "Odoo Export" table's normal path).
    - only `channel_value` given (or neither) -> the original one-field
      behavior, unchanged: a single "Channel" column set to `channel_value`
      (or DEFAULT_ODOO_CHANNEL_VALUE)."""
    if extra_fields is not None:
        return extra_fields
    return [{
        "column": "Channel",
        "value": channel_value if channel_value is not None else DEFAULT_ODOO_CHANNEL_VALUE,
        "aliases": ("channel", "lead_channel", "source_channel"),
    }]


def extra_fields_from_settings(get_setting) -> list[dict]:
    """Resolves the configured Odoo extra-fields list from wherever the
    caller's settings live (normally Settings -> "Odoo Export"'s table,
    stored under db setting "odoo_extra_fields") - shared by SettingsScreen
    (to populate the table) and new_scrape.py's export flow (to build the
    kwargs for export_odoo_xlsx/xls), so both read the SAME resolved list
    instead of duplicating this migration logic.

    Migrates the older single-purpose "odoo_channel_value" setting the
    first time it's read, so a user who configured that BEFORE this file
    generalized "Channel" into a list doesn't silently lose their value.
    `get_setting` is any `(key, default) -> value` callable - normally
    `Database.get_setting`, passed in rather than imported so this module
    (the export layer) stays independent of the storage layer."""
    fields = get_setting("odoo_extra_fields", None)
    if fields is not None:
        return fields
    legacy_channel = get_setting("odoo_channel_value", None)
    if legacy_channel is not None:
        return [{
            "column": "Channel", "value": legacy_channel,
            "aliases": ["channel", "lead_channel", "source_channel"],
        }]
    return [dict(f) for f in DEFAULT_ODOO_EXTRA_FIELDS]


def _lead_row_for_odoo(flat: dict, external_id: str, extra_fields: list[dict]) -> list:
    """`extra_fields` is the already-resolved list (see
    _resolve_extra_fields) - a general form of the old single "Channel"
    special-case: each entry is {"column", "value", "aliases"?}, applied
    with the SAME precedence as every stock column - the lead's own
    scraped field (matched by alias, or the lowercased column name if no
    aliases given) wins; the fixed "value" is the fallback."""
    lower = {str(k).lower(): v for k, v in flat.items()}
    row = []
    for col in ODOO_STANDARD_COLUMNS:
        if col == "External ID":
            row.append(external_id)
            continue
        value = None
        for key in _ODOO_FIELD_MAP.get(col, ()):
            if key in lower and lower[key] not in (None, ""):
                value = lower[key]
                break
        row.append(_stringify(value) if value is not None else "")
    for field in extra_fields:
        aliases = field.get("aliases") or (str(field["column"]).lower(),)
        value = None
        for key in aliases:
            key = str(key).lower()
            if key in lower and lower[key] not in (None, ""):
                value = lower[key]
                break
        if value is None:
            value = field.get("value")  # lead has none of its own -> fall back to the fixed setting
        row.append(_stringify(value) if value is not None else "")
    return row


# The "Import FAQ" sheet reproduced verbatim from Odoo's own crm.lead import
# template, so the exported file guides a user through Odoo's import screen
# exactly the way the original template does.
_ODOO_IMPORT_FAQ = [
    "How to customize the file?",
    "Add, remove and sort columns as you want.",
    "Keep the header (first row) as is for columns you need to keep. Those column labels will be automatically matched in Odoo.",
    "Put any title to your new columns. You can select the fields to match when importing in Odoo.",
    "Mandatory fields to import are the mandatory fields not populated with default values through the system.",
    "It is not recommended to remove the 'ID column' (see here below).",
    "",
    "How to import this file?",
    "Keep the file type as '.xls' as fields formatting is automatic.",
    "If you import in '.csv', double check that the formatting is correctly interpreted in Odoo (encoding format, date format, separators, etc.).",
    "",
    "What is the 'External ID' for?",
    "External ID are unique identifiers for imported records.",
    "If an ID is set to every record, you can reimport the same file several time and Odoo will update records instead of creating new ones if the ID already exists.",
    "You can create your own ID sequence or use the one of your previous software to ease the migration process. Otherwise extend the structure suggested in this file to all your records (by simple drag).",
    "Using ID,  you can safely point out related records from other database tables (e.g. vendors or tags when importing products). You can also use the record name but the import will stop in case of several matching.",
    "",
    "How to import many2one and many2many relationships (e.g. tags)?",
    "Use value names or IDs.",
    'You can create such related records on the fly if they don\'t exist by checking the box "Create if doesn\'t exist" showing up in the column.',
    "To import several m2m values, separate them with a comma without any spacing (e.g. for customer tags: B2B,Medium Size,Clothes Industry).",
    "",
    "How to import one2many relationships (e.g. orders or invoices with several lines)?",
    'To import fields of one2many fields (unit price, quantity, etc. of order/invoice/pricelist lines), make sure "Show fields of relation fields" is checked in the import interface. It allows to map unmatched one2many columns.',
    "You need to reserve one row for each one2many record. Fields of parent level must be left empty so that the system knows there are several o2m lines to import for the same record.",
    "",
    "How to import translated values for translatable fields?",
    "If you install several languages in Odoo, you can set translations for your master data (product names, descriptions, etc.) by clicking the little Earth icon showing up in the field and in the mapping zone of the import screen.",
    "In the import screen, you will be suggested to choose the translations to target with the column to import.",
    "You can therefore use several columns for the same field, each of them pointing specific translations.",
    "",
    "Need more information? Check out our full FAQ at:",
    "https://www.odoo.com/documentation/user/online/general/base_import/import_faq.html",
]


def export_odoo_xlsx(
    results: Iterable[dict], dest: str | Path, on_progress: ProgressCB = None,
    channel_value: str = None, extra_fields: list[dict] | None = None,
) -> int:
    """Exports in the same format/layout as Odoo's own CRM Lead import
    template (External ID / Name / Company Name / Contact Name / Email /
    Job Position / Phone / Mobile / Street / Street2 / City / State / Zip /
    Country / Website / Notes, plus an 'Import FAQ' sheet), with one column
    per entry in `extra_fields` appended after Notes for any field THIS
    user's Odoo instance additionally requires (Channel, Source, Sales
    Team, ...- see DEFAULT_ODOO_EXTRA_FIELDS' docstring above), so the
    output can be imported straight into Odoo's CRM -> Leads -> Import
    screen.

    `extra_fields` is the general form, normally supplied from Settings ->
    "Odoo Export"'s table (db setting "odoo_extra_fields"). `channel_value`
    is kept only for existing callers that predate that table - passing it
    alone still produces the original single "Channel" column; pass
    `extra_fields` explicitly (even `[]`, for "no extra columns at all") to
    take over the whole list, in which case `channel_value` is ignored."""
    from openpyxl import Workbook

    dest = Path(dest)
    rows = list(_rows(results))
    fields = _resolve_extra_fields(extra_fields, channel_value)
    columns = ODOO_STANDARD_COLUMNS + [f["column"] for f in fields]

    wb = Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.append(columns)
    count = 0
    for i, row in enumerate(rows, start=1):
        ws.append(_lead_row_for_odoo(row, f"crm_lead_{i}", fields))
        count += 1
        if on_progress and count % 100 == 0:
            on_progress(count)

    faq = wb.create_sheet("Import FAQ")
    for line in _ODOO_IMPORT_FAQ:
        faq.append([line])

    wb.save(str(dest))
    if on_progress:
        on_progress(count)
    return count


def export_odoo_xls(
    results: Iterable[dict], dest: str | Path, on_progress: ProgressCB = None,
    channel_value: str = None, extra_fields: list[dict] | None = None,
) -> int:
    """Same layout/columns as export_odoo_xlsx() above, but written as a
    REAL legacy .xls (BIFF8 binary) file instead of .xlsx (OOXML) -
    "عايزه يطلع XLS مش XSLS": Odoo's own downloadable CRM Lead import
    template is itself a "crm_lead 1.xls" file (see this file's module
    docstring / the very first file the user uploaded), and some Odoo
    versions/import flows are pickier about accepting that exact legacy
    format over .xlsx. openpyxl (used above) can only WRITE .xlsx - it
    dropped .xls support entirely - so this uses xlwt instead, the
    library that still writes genuine legacy Excel binary files. This is
    NOT just export_odoo_xlsx()'s file renamed to '.xls' (that would be
    an .xlsx file wearing the wrong extension, which real Excel/Odoo can
    still detect and may reject or mis-parse) - the bytes on disk are an
    actual .xls workbook.

    xlwt is an old, no-longer-updated library (last released 2019) but
    is still the correct tool for this: the legacy .xls format itself
    hasn't changed, and no actively-maintained library replaced it for
    WRITING (only reading, e.g. xlrd). Its one real limitation versus
    openpyxl is a 65,536-row-per-sheet ceiling (the old Excel format's
    own limit, not something LOGY imposes) - fine for the Odoo import use
    case (Odoo's own bulk-import guidance recommends batches well under
    that anyway), but worth knowing if this function is ever reused
    elsewhere for very large exports."""
    try:
        import xlwt
    except ImportError as e:
        raise ImportError(
            "تصدير Odoo بصيغة .xls محتاج مكتبة xlwt ومش متثبتة. افتح الطرفية وشغّل:\n\n"
            "pip install xlwt\n\nوبعدين جرّب التصدير تاني."
        ) from e

    dest = Path(dest)
    rows = list(_rows(results))
    fields = _resolve_extra_fields(extra_fields, channel_value)
    columns = ODOO_STANDARD_COLUMNS + [f["column"] for f in fields]

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Template")
    for col_i, col_name in enumerate(columns):
        ws.write(0, col_i, col_name)
    count = 0
    for i, row in enumerate(rows, start=1):
        values = _lead_row_for_odoo(row, f"crm_lead_{i}", fields)
        for col_i, value in enumerate(values):
            ws.write(i, col_i, value)
        count += 1
        if on_progress and count % 100 == 0:
            on_progress(count)

    faq = wb.add_sheet("Import FAQ")
    for row_i, line in enumerate(_ODOO_IMPORT_FAQ):
        faq.write(row_i, 0, line)

    wb.save(str(dest))
    if on_progress:
        on_progress(count)
    return count


EXPORTERS = {
    "csv": export_csv,
    "json": export_json,
    "jsonl": export_jsonl,
    "xlsx": export_xlsx,
    "odoo_xlsx": export_odoo_xlsx,
    "odoo_xls": export_odoo_xls,
}


def export(fmt: str, results: Iterable[dict], dest: str | Path, on_progress: ProgressCB = None, **kwargs) -> int:
    fmt = fmt.lower().lstrip(".")
    if fmt not in EXPORTERS:
        raise ValueError(f"صيغة تصدير غير مدعومة: {fmt}. المتاح: {', '.join(EXPORTERS)}")
    # **kwargs lets a caller pass exporter-specific options (currently
    # only export_odoo_xlsx's channel_value - see new_scrape.py's
    # _export_results()) without every OTHER exporter needing to accept
    # and ignore them.
    return EXPORTERS[fmt](results, dest, on_progress, **kwargs)


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
