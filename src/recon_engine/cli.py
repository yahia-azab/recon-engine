"""Command line entry point.

    python -m recon_engine.cli --gl data/gl_extract.xlsx --subledger data/subledger_extract.xlsx --out output/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ReconConfig
from .matching import Matcher
from .normalise import load_extract
from .report import write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recon-engine",
        description="Reconcile a general ledger extract against a sub-ledger extract.",
    )
    parser.add_argument("--gl", type=Path, required=True, help="general ledger extract (xlsx/csv)")
    parser.add_argument("--subledger", type=Path, required=True, help="sub-ledger extract (xlsx/csv)")
    parser.add_argument("--out", type=Path, default=Path("output"), help="output directory")
    parser.add_argument("--name", type=str, default="reconciliation", help="output file stem")

    tuning = parser.add_argument_group("matching tolerances")
    tuning.add_argument("--amount-tolerance-pct", type=float, default=ReconConfig.amount_tolerance_pct)
    tuning.add_argument("--amount-tolerance-abs", type=float, default=ReconConfig.amount_tolerance_abs)
    tuning.add_argument("--date-window-days", type=int, default=ReconConfig.date_window_days)
    tuning.add_argument("--fuzzy-threshold", type=float, default=ReconConfig.fuzzy_threshold)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    for path in (args.gl, args.subledger):
        if not path.exists():
            print(f"error: file not found: {path}", file=sys.stderr)
            return 2

    config = ReconConfig(
        amount_tolerance_pct=args.amount_tolerance_pct,
        amount_tolerance_abs=args.amount_tolerance_abs,
        date_window_days=args.date_window_days,
        fuzzy_threshold=args.fuzzy_threshold,
    )

    print("Loading extracts")
    gl = load_extract(args.gl, "GL")
    sub = load_extract(args.subledger, "SUB")
    print(f"  GL         : {len(gl):>5} lines, total {gl['amount'].sum():,.2f}")
    print(f"  Sub-ledger : {len(sub):>5} lines, total {sub['amount'].sum():,.2f}")

    print("\nMatching")
    result = Matcher(gl, sub, config).run()

    out_path = write_report(result, args.out / f"{args.name}.xlsx")

    matched_gl = len(result.matched_gl_ids)
    rate = matched_gl / result.gl_count if result.gl_count else 0.0
    print("\nResult")
    print(f"  matched      : {matched_gl}/{result.gl_count} GL lines ({rate:.1%})")
    print(f"  exceptions   : {len(result.exceptions)}")
    print(f"  difference   : {result.gl_total - result.sub_total:,.2f}")
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
