"""Loading and normalisation of ledger extracts.

Two systems never agree on column names, reference formats or date types. Everything
downstream assumes a single canonical shape, so all the mess is dealt with here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# Canonical columns every extract is mapped onto.
CANONICAL = ["line_id", "entity", "reference", "ref_norm", "txn_date", "amount", "currency", "description"]

# Source column name -> canonical name. Lower-cased, punctuation-stripped on lookup.
COLUMN_ALIASES: dict[str, str] = {
    # identifiers
    "documentno": "line_id", "docno": "line_id", "itemno": "line_id",
    "lineid": "line_id", "id": "line_id", "documentnumber": "line_id",
    # entity
    "entity": "entity", "companycode": "entity", "company": "entity", "bukrs": "entity",
    # reference
    "reference": "reference", "ref": "reference", "invoiceno": "reference",
    "invoicenumber": "reference", "documentreference": "reference", "xblnr": "reference",
    # date
    "postingdate": "txn_date", "documentdate": "txn_date", "date": "txn_date",
    "transactiondate": "txn_date", "valuedate": "txn_date", "budat": "txn_date",
    # amount
    "amount": "amount", "value": "amount", "localamount": "amount",
    "amountinlocalcurrency": "amount", "dmbtr": "amount", "debitcredit": "amount",
    # currency
    "currency": "currency", "curr": "currency", "waers": "currency",
    # description
    "description": "description", "text": "description", "narrative": "description",
    "itemtext": "description", "sgtxt": "description",
}

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_LEADING_ZEROS = re.compile(r"^0+(?=\d)")


def normalise_reference(value: object) -> str:
    """Reduce a reference to a comparable core.

    Strips punctuation, spacing and case, then drops leading zeros so that
    ``INV-A000123``, ``inv a123`` and ``INVA123`` all collapse to ``INVA123``.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = _NON_ALNUM.sub("", str(value)).upper()
    return _LEADING_ZEROS.sub("", text)


def _canonical_key(column: str) -> str:
    return _NON_ALNUM.sub("", str(column)).lower()


def load_extract(path: Path, source: str) -> pd.DataFrame:
    """Load an Excel/CSV extract and map it onto the canonical shape.

    Args:
        path: file to read.
        source: short tag ("GL" / "SUB") used to build unique line ids.
    """
    if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        raw = pd.read_excel(path)
    elif path.suffix.lower() == ".csv":
        raw = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported extract format: {path.suffix}")

    mapping = {}
    for column in raw.columns:
        canonical = COLUMN_ALIASES.get(_canonical_key(column))
        # First alias wins: later duplicates (e.g. two date columns) are ignored
        # rather than silently overwriting a column already claimed.
        if canonical and canonical not in mapping.values():
            mapping[column] = canonical

    df = raw.rename(columns=mapping)

    missing = {"amount", "txn_date"} - set(df.columns)
    if missing:
        raise ValueError(
            f"{path.name}: could not identify required column(s) {sorted(missing)}. "
            f"Found columns: {list(raw.columns)}"
        )

    for column in CANONICAL:
        if column not in df.columns:
            df[column] = ""

    df["ref_norm"] = df["reference"].map(normalise_reference)
    df["txn_date"] = pd.to_datetime(df["txn_date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").round(2)
    df["entity"] = df["entity"].astype(str).str.strip().str.upper()
    df["source"] = source
    df["line_id"] = [f"{source}-{i:06d}" for i in range(1, len(df) + 1)]

    bad_amounts = df["amount"].isna().sum()
    bad_dates = df["txn_date"].isna().sum()
    if bad_amounts or bad_dates:
        print(f"  warning: {path.name} has {bad_amounts} unparseable amount(s), {bad_dates} unparseable date(s)")

    return df[CANONICAL + ["source"]].copy()
