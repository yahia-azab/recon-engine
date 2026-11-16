"""Reconciliation tolerances and thresholds.

Every number here is a policy decision an accountant should own, not a constant buried
in matching code. Keeping them in one place means the engine can be re-tuned per
account, entity or client without touching logic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReconConfig:
    """Matching policy for a reconciliation run."""

    # --- amount tolerance -------------------------------------------------------
    # An item is "same amount" if it is within EITHER bound. Relative tolerance
    # absorbs FX and rounding on large values; absolute tolerance stops tiny items
    # from being held to an impossibly tight relative standard.
    amount_tolerance_pct: float = 0.005  # 0.5%
    amount_tolerance_abs: float = 1.00

    # --- timing -----------------------------------------------------------------
    # How far apart two dates may be and still be considered the same event.
    # Cross-system posting lag is normal; 10 days covers month-end cut-off drift.
    date_window_days: int = 10

    # --- fuzzy reference --------------------------------------------------------
    # Similarity above which two normalised references are treated as the same.
    # Deliberately high: a false match in a reconciliation is worse than a miss,
    # because a miss gets reviewed and a false match does not.
    fuzzy_threshold: float = 0.88

    # --- partial settlement -----------------------------------------------------
    # Maximum sub-ledger items that may be summed against a single GL posting.
    max_split_items: int = 6

    def amounts_agree(self, a: float, b: float) -> bool:
        """True if two amounts agree within either tolerance."""
        diff = abs(a - b)
        if diff <= self.amount_tolerance_abs:
            return True
        scale = max(abs(a), abs(b))
        return scale > 0 and (diff / scale) <= self.amount_tolerance_pct

    def is_exact_amount(self, a: float, b: float) -> bool:
        """True only for genuine equality at cent precision."""
        return round(abs(a - b), 2) == 0.0
