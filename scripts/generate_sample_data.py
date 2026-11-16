"""Generate a realistic synthetic reconciliation dataset.

Produces a general ledger extract and a sub-ledger extract that disagree in all the
ways real extracts disagree -- timing differences, FX drift, split settlements,
duplicate postings and genuine breaks -- plus an answer key recording exactly which
discrepancy was injected where.

The answer key is what makes the engine testable: we can assert that the matcher
found what we know is there, and nothing we know is not.

No real financial data is used or required.

Usage:
    python scripts/generate_sample_data.py --out data/ --pairs 500 --seed 42
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

ENTITIES = ["AE10", "AE31", "EG20", "SA15", "QA31"]
CURRENCIES = {"AE10": "AED", "AE31": "AED", "EG20": "EGP", "SA15": "SAR", "QA31": "QAR"}

COST_CENTRES = ["CC1000", "CC1100", "CC2400", "CC3300", "CC4100"]
GL_ACCOUNTS = ["21010100", "21010200", "13040500", "13040600", "24500100"]

VENDOR_NAMES = [
    "Gulf Facilities Management", "Horizon Logistics", "Delta Office Supplies",
    "Nile Technical Services", "Pinnacle Contracting", "Meridian IT Solutions",
    "Crescent Maintenance Co", "Oasis Catering Services", "Vertex Engineering",
    "Summit Security Services",
]

DESCRIPTIONS = [
    "Monthly service fee", "Maintenance contract", "Office consumables",
    "Professional services", "Equipment rental", "Utilities recharge",
    "Software licence", "Transport and freight", "Cleaning services",
    "Security services",
]


@dataclass
class InjectionCounts:
    """How many of each discrepancy type to inject."""

    timing: int = 40
    fx_variance: int = 25
    partial_split: int = 20
    duplicate: int = 15
    gl_only: int = 18
    subledger_only: int = 22


@dataclass
class Dataset:
    gl_rows: list[dict] = field(default_factory=list)
    sub_rows: list[dict] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _amount(rng: random.Random) -> float:
    """A plausible invoice amount, weighted toward smaller values."""
    magnitude = rng.choices([2, 3, 4, 5], weights=[30, 45, 20, 5])[0]
    return round(rng.uniform(1, 10) * (10**magnitude), 2)


def _reference(rng: random.Random, seq: int) -> str:
    return f"INV-{rng.choice(['A', 'B', 'C'])}{seq:06d}"


def _drift_reference(rng: random.Random, ref: str) -> str:
    """Mangle a reference the way a second system usually does."""
    style = rng.choice(["strip_prefix", "no_dash", "lower", "pad", "suffix"])
    if style == "strip_prefix":
        return ref.split("-", 1)[1]
    if style == "no_dash":
        return ref.replace("-", "")
    if style == "lower":
        return ref.lower()
    if style == "pad":
        return f" {ref} "
    return f"{ref}/1"


def _base_row(rng: random.Random, seq: int, period_start: date) -> dict:
    entity = rng.choice(ENTITIES)
    posting = period_start + timedelta(days=rng.randint(0, 27))
    return {
        "entity": entity,
        "currency": CURRENCIES[entity],
        "gl_account": rng.choice(GL_ACCOUNTS),
        "cost_centre": rng.choice(COST_CENTRES),
        "reference": _reference(rng, seq),
        "posting_date": posting,
        "amount": _amount(rng),
        "description": rng.choice(DESCRIPTIONS),
        "vendor": rng.choice(VENDOR_NAMES),
    }


def _gl_row(row: dict, doc_no: int) -> dict:
    return {
        "Document No": f"19{doc_no:08d}",
        "Entity": row["entity"],
        "GL Account": row["gl_account"],
        "Cost Centre": row["cost_centre"],
        "Reference": row["reference"],
        "Posting Date": row["posting_date"],
        "Currency": row["currency"],
        "Amount": row["amount"],
        "Description": row["description"],
    }


def _sub_row(row: dict, item_no: int) -> dict:
    return {
        "Item No": f"SL{item_no:08d}",
        "Entity": row["entity"],
        "Vendor": row["vendor"],
        "Reference": row["reference"],
        "Document Date": row["posting_date"],
        "Currency": row["currency"],
        "Amount": row["amount"],
        "Description": row["description"],
    }


# --------------------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------------------


def build_dataset(
    pairs: int,
    injections: InjectionCounts,
    period_start: date,
    seed: int,
) -> Dataset:
    rng = random.Random(seed)
    ds = Dataset()
    seq = doc_no = item_no = 1

    def record(kind: str, ref: str, note: str) -> None:
        ds.answers.append({"discrepancy_type": kind, "reference": ref, "detail": note})

    # ---- clean matched pairs -----------------------------------------------------
    for _ in range(pairs):
        row = _base_row(rng, seq, period_start)
        ds.gl_rows.append(_gl_row(row, doc_no))
        ds.sub_rows.append(_sub_row(row, item_no))
        seq, doc_no, item_no = seq + 1, doc_no + 1, item_no + 1

    # ---- timing differences: same item, sub-ledger dated in the next period -------
    for _ in range(injections.timing):
        row = _base_row(rng, seq, period_start)
        ds.gl_rows.append(_gl_row(row, doc_no))
        lag = rng.randint(1, 9)
        shifted = dict(row, posting_date=row["posting_date"] + timedelta(days=lag))
        ds.sub_rows.append(_sub_row(shifted, item_no))
        record("timing", row["reference"], f"sub-ledger dated {lag} day(s) later")
        seq, doc_no, item_no = seq + 1, doc_no + 1, item_no + 1

    # ---- FX / rounding variance: same item, small amount delta -------------------
    for _ in range(injections.fx_variance):
        row = _base_row(rng, seq, period_start)
        ds.gl_rows.append(_gl_row(row, doc_no))
        delta_pct = rng.uniform(-0.004, 0.004)
        drifted = dict(row, amount=round(row["amount"] * (1 + delta_pct), 2))
        ds.sub_rows.append(_sub_row(drifted, item_no))
        record("fx_variance", row["reference"], f"amount drift {delta_pct * 100:+.3f}%")
        seq, doc_no, item_no = seq + 1, doc_no + 1, item_no + 1

    # ---- partial / split: one GL posting, several sub-ledger settlements ---------
    for _ in range(injections.partial_split):
        row = _base_row(rng, seq, period_start)
        ds.gl_rows.append(_gl_row(row, doc_no))
        n_parts = rng.randint(2, 4)
        remaining = row["amount"]
        for part in range(n_parts):
            if part == n_parts - 1:
                part_amount = round(remaining, 2)
            else:
                part_amount = round(remaining * rng.uniform(0.2, 0.5), 2)
                remaining = round(remaining - part_amount, 2)
            piece = dict(row, amount=part_amount)
            ds.sub_rows.append(_sub_row(piece, item_no))
            item_no += 1
        record("partial_split", row["reference"], f"settled in {n_parts} instalments")
        seq, doc_no = seq + 1, doc_no + 1

    # ---- duplicates: posted twice in the sub-ledger ------------------------------
    for _ in range(injections.duplicate):
        row = _base_row(rng, seq, period_start)
        ds.gl_rows.append(_gl_row(row, doc_no))
        ds.sub_rows.append(_sub_row(row, item_no))
        ds.sub_rows.append(_sub_row(row, item_no + 1))
        record("duplicate", row["reference"], "posted twice in sub-ledger")
        seq, doc_no, item_no = seq + 1, doc_no + 1, item_no + 2

    # ---- genuine breaks ----------------------------------------------------------
    for _ in range(injections.gl_only):
        row = _base_row(rng, seq, period_start)
        ds.gl_rows.append(_gl_row(row, doc_no))
        record("gl_only", row["reference"], "no sub-ledger counterpart")
        seq, doc_no = seq + 1, doc_no + 1

    for _ in range(injections.subledger_only):
        row = _base_row(rng, seq, period_start)
        ds.sub_rows.append(_sub_row(row, item_no))
        record("subledger_only", row["reference"], "no GL counterpart")
        seq, item_no = seq + 1, item_no + 1

    # ---- reference format drift on a slice of the clean pairs --------------------
    drift_targets = rng.sample(range(pairs), k=min(30, pairs))
    for idx in drift_targets:
        original = ds.sub_rows[idx]["Reference"]
        ds.sub_rows[idx]["Reference"] = _drift_reference(rng, original)
        record("reference_drift", original, f"sub-ledger shows {ds.sub_rows[idx]['Reference']!r}")

    rng.shuffle(ds.gl_rows)
    rng.shuffle(ds.sub_rows)
    return ds


def write_outputs(ds: Dataset, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    gl = pd.DataFrame(ds.gl_rows)
    sub = pd.DataFrame(ds.sub_rows)
    key = pd.DataFrame(ds.answers)

    gl.to_excel(out_dir / "gl_extract.xlsx", index=False)
    sub.to_excel(out_dir / "subledger_extract.xlsx", index=False)
    key.to_excel(out_dir / "answer_key.xlsx", index=False)

    print(f"GL extract        : {len(gl):>5} rows  -> {out_dir / 'gl_extract.xlsx'}")
    print(f"Sub-ledger extract: {len(sub):>5} rows  -> {out_dir / 'subledger_extract.xlsx'}")
    print(f"Answer key        : {len(key):>5} rows  -> {out_dir / 'answer_key.xlsx'}")
    print("\nInjected discrepancies:")
    for kind, count in key["discrepancy_type"].value_counts().items():
        print(f"  {kind:<18} {count:>4}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data"), help="output directory")
    parser.add_argument("--pairs", type=int, default=500, help="clean matched pairs")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (reproducible)")
    parser.add_argument("--period", type=str, default="2026-09-01", help="period start, YYYY-MM-DD")
    args = parser.parse_args()

    ds = build_dataset(
        pairs=args.pairs,
        injections=InjectionCounts(),
        period_start=date.fromisoformat(args.period),
        seed=args.seed,
    )
    write_outputs(ds, args.out)


if __name__ == "__main__":
    main()
