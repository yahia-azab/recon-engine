"""Excel reporting.

The output is written for the person who has to defend the reconciliation, not for the
person who ran it: a summary that states the position, an exception register grouped by
cause, and a full audit trail of every match and the rule that produced it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .matching import ReconResult


def _summary_frame(result: ReconResult) -> pd.DataFrame:
    matched_gl = len(result.matched_gl_ids)
    matched_sub = len(result.matched_sub_ids)
    exceptions_by_type = {}
    for exc in result.exceptions:
        exceptions_by_type[exc.exception_type] = exceptions_by_type.get(exc.exception_type, 0) + 1

    matched_value = round(sum(m.gl_amount for m in result.matches), 2)
    unexplained = round(result.gl_total - result.sub_total, 2)

    rows = [
        ("GL lines", result.gl_count),
        ("Sub-ledger lines", result.sub_count),
        ("GL total", result.gl_total),
        ("Sub-ledger total", result.sub_total),
        ("Difference", unexplained),
        ("", ""),
        ("GL lines matched", f"{matched_gl} ({matched_gl / result.gl_count:.1%})" if result.gl_count else "0"),
        ("Sub-ledger lines matched", f"{matched_sub} ({matched_sub / result.sub_count:.1%})" if result.sub_count else "0"),
        ("Matched value (GL side)", matched_value),
        ("Total matches", len(result.matches)),
        ("Total exceptions", len(result.exceptions)),
        ("", ""),
    ]

    for match_type in sorted({m.match_type for m in result.matches}):
        count = sum(1 for m in result.matches if m.match_type == match_type)
        rows.append((f"  matches — {match_type}", count))

    rows.append(("", ""))
    for exc_type, count in sorted(exceptions_by_type.items(), key=lambda kv: -kv[1]):
        rows.append((f"  exceptions — {exc_type}", count))

    return pd.DataFrame(rows, columns=["Metric", "Value"])


def _matches_frame(result: ReconResult) -> pd.DataFrame:
    if not result.matches:
        return pd.DataFrame(columns=["Match Type", "Confidence", "Entity", "Reference"])
    return pd.DataFrame(
        [
            {
                "Match Type": m.match_type,
                "Confidence": m.confidence,
                "Entity": m.entity,
                "Reference": m.reference,
                "GL Lines": ", ".join(m.gl_ids),
                "Sub-ledger Lines": ", ".join(m.sub_ids),
                "GL Amount": m.gl_amount,
                "Sub-ledger Amount": m.sub_amount,
                "Difference": m.amount_diff,
                "Date Diff (days)": m.date_diff_days,
                "Note": m.note,
            }
            for m in result.matches
        ]
    ).sort_values(["Match Type", "Entity", "Reference"])


def _exceptions_frame(result: ReconResult) -> pd.DataFrame:
    if not result.exceptions:
        return pd.DataFrame(columns=["Exception Type", "Source", "Entity", "Reference"])
    return pd.DataFrame(
        [
            {
                "Exception Type": e.exception_type,
                "Source": e.source,
                "Line ID": e.line_id,
                "Entity": e.entity,
                "Reference": e.reference,
                "Date": e.txn_date,
                "Amount": e.amount,
                "Description": e.description,
                "Note": e.note,
                "Status": "Open",
                "Assigned To": "",
                "Comment": "",
            }
            for e in result.exceptions
        ]
    ).sort_values(["Exception Type", "Entity", "Amount"], ascending=[True, True, False])


def write_report(result: ReconResult, out_path: Path) -> Path:
    """Write the reconciliation workbook and return its path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    summary = _summary_frame(result)
    matches = _matches_frame(result)
    exceptions = _exceptions_frame(result)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        exceptions.to_excel(writer, sheet_name="Exception Register", index=False)
        matches.to_excel(writer, sheet_name="Audit Trail", index=False)

        for sheet_name, frame in [
            ("Summary", summary),
            ("Exception Register", exceptions),
            ("Audit Trail", matches),
        ]:
            worksheet = writer.sheets[sheet_name]
            for idx, column in enumerate(frame.columns, start=1):
                values = frame[column].astype(str)
                width = max(len(str(column)), int(values.str.len().max()) if len(values) else 0)
                worksheet.column_dimensions[
                    worksheet.cell(row=1, column=idx).column_letter
                ].width = min(max(width + 2, 10), 50)
            worksheet.freeze_panes = "A2"

    return out_path
