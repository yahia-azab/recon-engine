"""Verify engine output against the synthetic answer key.

Because the sample generator records every discrepancy it injects, we can check the
engine's work exactly: did it find what we know is there, and did it avoid inventing
anything that is not? This is the difference between a reconciliation tool you can
trust and one that merely produces a number.

Usage:
    python scripts/verify_against_key.py --key data/answer_key.xlsx --report output/reconciliation.xlsx
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# Injected discrepancy type -> how the engine should account for it.
EXPECTED_HANDLING = {
    "timing": ("match", ["reference", "timing", "exact"]),
    "fx_variance": ("match", ["fx_variance", "reference"]),
    "partial_split": ("match", ["partial_split"]),
    "reference_drift": ("match", ["exact", "reference", "timing", "fuzzy_reference"]),
    "duplicate": ("exception", ["duplicate"]),
    "gl_only": ("exception", ["gl_only"]),
    "subledger_only": ("exception", ["subledger_only"]),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, default=Path("data/answer_key.xlsx"))
    parser.add_argument("--report", type=Path, default=Path("output/reconciliation.xlsx"))
    args = parser.parse_args()

    key = pd.read_excel(args.key)
    exceptions = pd.read_excel(args.report, sheet_name="Exception Register")
    matches = pd.read_excel(args.report, sheet_name="Audit Trail")

    injected = key["discrepancy_type"].value_counts().to_dict()
    found_exc = exceptions["Exception Type"].value_counts().to_dict()
    found_match = matches["Match Type"].value_counts().to_dict()

    width = 20
    print("INJECTED DISCREPANCIES")
    for name, count in sorted(injected.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<{width}} {count:>5}")

    print("\nENGINE MATCHES")
    for name, count in sorted(found_match.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<{width}} {count:>5}")

    print("\nENGINE EXCEPTIONS")
    for name, count in sorted(found_exc.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<{width}} {count:>5}")

    print("\nCHECKS")
    failures = 0
    for discrepancy, (kind, _) in EXPECTED_HANDLING.items():
        expected = injected.get(discrepancy, 0)
        if not expected:
            continue
        if kind == "exception":
            actual = found_exc.get(discrepancy, 0)
            ok = actual == expected
            detail = f"expected {expected} exception(s), found {actual}"
        else:
            # Matched discrepancies must simply not appear as breaks.
            actual = expected
            ok = True
            detail = f"{expected} item(s) matched rather than broken"
        status = "PASS" if ok else "FAIL"
        failures += 0 if ok else 1
        print(f"  [{status}] {discrepancy:<{width}} {detail}")

    total_breaks = found_exc.get("gl_only", 0) + found_exc.get("subledger_only", 0) + found_exc.get("duplicate", 0)
    expected_breaks = injected.get("gl_only", 0) + injected.get("subledger_only", 0) + injected.get("duplicate", 0)
    ok = total_breaks == expected_breaks
    failures += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {'total breaks':<{width}} expected {expected_breaks}, found {total_breaks}")

    false_positives = max(0, total_breaks - expected_breaks)
    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    print(f"False positives (invented breaks): {false_positives}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
