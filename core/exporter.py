"""
XLSX export for scored jobs (Phase 1.6b).

Exports all jobs that have a score row, color-coded by priority_bucket.
Requires openpyxl (already installed).

Usage:
    from core.exporter import export_jobs_xlsx
    path = export_jobs_xlsx()          # → outputs/exports/jobs_<timestamp>.xlsx
    path = export_jobs_xlsx("out.xlsx")  # explicit path
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core import config, database

# ── Bucket color palette ───────────────────────────────────────────────────────
# ARGB hex strings (no leading #). Alpha = FF (fully opaque).

_BUCKET_FILL: dict[str, PatternFill] = {
    b: PatternFill(start_color=c, end_color=c, fill_type="solid")
    for b, c in {
        "Apply Immediately": "FF00C853",   # vivid green
        "High Priority":     "FFB9F6CA",   # light green
        "Medium Priority":   "FFFFD740",   # amber
        "Low Priority":      "FFFFAB40",   # orange
        "Ignore":            "FFE0E0E0",   # mid-grey
        "Rejected":          "FFFFCDD2",   # light red/pink
    }.items()
}

_HEADER_FILL = PatternFill(start_color="FF263238", end_color="FF263238", fill_type="solid")
_HEADER_FONT = Font(bold=True, color="FFFFFFFF")

# ── Column definitions ────────────────────────────────────────────────────────
# (field_key, header_label, column_width)

_COLUMNS: list[tuple[str, str, int]] = [
    ("priority_bucket",          "Bucket",          18),
    ("total_score",              "Score",            7),
    ("eligibility_score",        "Elig. Score",      11),
    ("title",                    "Title",            38),
    ("company",                  "Company",          22),
    ("location",                 "Location",         20),
    ("posted_date",              "Posted",           12),
    ("role_category",            "Role Category",    22),
    ("track",                    "Track",            10),
    ("language_gate",            "Lang Gate",        11),
    ("visa_gate",                "Visa Gate",        10),
    ("detected_language",        "Detected Lang",    14),
    ("language_risk",            "Lang Risk",        11),
    ("eligibility_status",       "Elig. Status",     13),
    ("track_alignment_score",    "Track",             7),
    ("skill_match",              "Skills",            7),
    ("mba_relevance_score",      "MBA",               6),
    ("company_quality_score",    "Co. Quality",      11),
    ("intl_friendliness_score",  "Intl",              6),
    ("pivot_bonus_score",        "Pivot",             7),
    ("explanation",              "Explanation",      55),
    ("url",                      "URL",              45),
]


def export_jobs_xlsx(output_path: str | None = None) -> str:
    """
    Export all scored jobs to XLSX, color-coded by priority_bucket.

    Rows are ordered: Apply Immediately → High → Medium → Low → Ignore → Rejected.
    Within each bucket, rows are ordered by total_score descending.

    Returns the absolute path of the written file.
    """
    rows = database.get_scored_jobs_for_export()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Scored Jobs"

    # ── Header row ─────────────────────────────────────────────────────────────
    headers = [col[1] for col in _COLUMNS]
    ws.append(headers)
    for col_idx, (_, _, width) in enumerate(_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.row_dimensions[1].height = 20
    ws.freeze_panes = "A2"

    # ── Data rows ──────────────────────────────────────────────────────────────
    for row_data in rows:
        values = [row_data.get(key) for key, _, _ in _COLUMNS]
        ws.append(values)

        bucket = row_data.get("priority_bucket") or "Ignore"
        fill   = _BUCKET_FILL.get(bucket)
        row_n  = ws.max_row

        for col_idx in range(1, len(_COLUMNS) + 1):
            cell = ws.cell(row=row_n, column=col_idx)
            if fill:
                cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=False)

        # Wrap explanation column only
        exp_col = next(
            i + 1 for i, (k, _, _) in enumerate(_COLUMNS) if k == "explanation"
        )
        ws.cell(row=row_n, column=exp_col).alignment = Alignment(
            vertical="top", wrap_text=True
        )

    # ── Summary sheet ──────────────────────────────────────────────────────────
    ws_sum = wb.create_sheet("Summary")
    _write_summary_sheet(ws_sum, rows)

    # ── Write file ─────────────────────────────────────────────────────────────
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(config.OUTPUTS_DIR) / "exports" / f"jobs_{timestamp}.xlsx"
    else:
        out = Path(output_path)

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out))
    return str(out.resolve())


def _write_summary_sheet(ws, rows: list[dict]) -> None:
    """Append a compact bucket-count summary to the Summary worksheet."""
    from core.scorer import BUCKET_ORDER

    counts: dict[str, int] = {}
    for row in rows:
        b = row.get("priority_bucket") or "Ignore"
        counts[b] = counts.get(b, 0) + 1

    ws.append(["Bucket", "Count"])
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 8

    for bucket in BUCKET_ORDER:
        n = counts.get(bucket, 0)
        ws.append([bucket, n])
        fill = _BUCKET_FILL.get(bucket)
        if fill:
            for cell in ws[ws.max_row]:
                cell.fill = fill

    ws.append([])
    ws.append(["Total", len(rows)])
    ws.append(["Generated", datetime.now().strftime("%Y-%m-%d %H:%M")])
