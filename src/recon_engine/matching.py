"""The matching engine.

Five passes run in descending order of confidence. Each pass removes what it matches
from the pool before the next runs, so a loose rule can never steal an item that a
stricter rule would have claimed. Whatever survives all five is classified as a break.

The ordering is the whole design. A single matching rule either misses real matches or
invents false ones; in reconciliation a false match is the more expensive error,
because a miss gets reviewed by a human and a false match does not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations

import pandas as pd

from .config import ReconConfig

# Match types, ordered by confidence.
EXACT = "exact"
REFERENCE = "reference"
FX_VARIANCE = "fx_variance"
TIMING = "timing"
PARTIAL_SPLIT = "partial_split"
FUZZY = "fuzzy_reference"

# Exception types.
DUPLICATE = "duplicate"
GL_ONLY = "gl_only"
SUBLEDGER_ONLY = "subledger_only"


@dataclass
class Match:
    match_type: str
    confidence: float
    gl_ids: list[str]
    sub_ids: list[str]
    entity: str
    reference: str
    gl_amount: float
    sub_amount: float
    amount_diff: float
    date_diff_days: int | None
    note: str = ""


@dataclass
class Exception_:
    exception_type: str
    line_id: str
    source: str
    entity: str
    reference: str
    amount: float
    txn_date: object
    description: str
    note: str = ""


@dataclass
class ReconResult:
    matches: list[Match] = field(default_factory=list)
    exceptions: list[Exception_] = field(default_factory=list)
    gl_total: float = 0.0
    sub_total: float = 0.0
    gl_count: int = 0
    sub_count: int = 0

    @property
    def matched_gl_ids(self) -> set[str]:
        return {i for m in self.matches for i in m.gl_ids}

    @property
    def matched_sub_ids(self) -> set[str]:
        return {i for m in self.matches for i in m.sub_ids}


class Matcher:
    """Reconciles a GL extract against a sub-ledger extract."""

    def __init__(self, gl: pd.DataFrame, sub: pd.DataFrame, config: ReconConfig | None = None):
        self.config = config or ReconConfig()
        self.gl = gl.set_index("line_id", drop=False)
        self.sub = sub.set_index("line_id", drop=False)
        self.open_gl: set[str] = set(self.gl.index)
        self.open_sub: set[str] = set(self.sub.index)
        self.matches: list[Match] = []

    # -- helpers ---------------------------------------------------------------

    def _record(self, match: Match) -> None:
        self.matches.append(match)
        self.open_gl.difference_update(match.gl_ids)
        self.open_sub.difference_update(match.sub_ids)

    @staticmethod
    def _days_between(a, b) -> int | None:
        if pd.isna(a) or pd.isna(b):
            return None
        return int(abs((a - b).days))

    def _pair(self, gl_id: str, sub_ids: list[str], match_type: str, confidence: float, note: str = "") -> Match:
        g = self.gl.loc[gl_id]
        sub_rows = [self.sub.loc[s] for s in sub_ids]
        sub_amount = round(sum(float(r["amount"]) for r in sub_rows), 2)
        date_diff = self._days_between(g["txn_date"], sub_rows[0]["txn_date"]) if len(sub_rows) == 1 else None
        return Match(
            match_type=match_type,
            confidence=confidence,
            gl_ids=[gl_id],
            sub_ids=list(sub_ids),
            entity=str(g["entity"]),
            reference=str(g["reference"]),
            gl_amount=float(g["amount"]),
            sub_amount=sub_amount,
            amount_diff=round(float(g["amount"]) - sub_amount, 2),
            date_diff_days=date_diff,
            note=note,
        )

    def _index_open_sub(self, key_fn) -> dict:
        index = defaultdict(list)
        for sub_id in self.open_sub:
            index[key_fn(self.sub.loc[sub_id])].append(sub_id)
        return index

    # -- pass 1: exact ---------------------------------------------------------

    def pass_exact(self) -> int:
        """Reference, amount and date all agree. Highest possible confidence."""
        index = self._index_open_sub(
            lambda r: (r["entity"], r["ref_norm"], round(float(r["amount"]), 2), r["txn_date"])
        )
        found = 0
        for gl_id in sorted(self.open_gl):
            g = self.gl.loc[gl_id]
            key = (g["entity"], g["ref_norm"], round(float(g["amount"]), 2), g["txn_date"])
            candidates = [s for s in index.get(key, []) if s in self.open_sub]
            if candidates:
                self._record(self._pair(gl_id, [candidates[0]], EXACT, 1.0))
                found += 1
        return found

    # -- pass 2: reference + tolerant amount ----------------------------------

    def pass_reference(self) -> int:
        """Reference agrees; amount within tolerance. Catches FX and rounding drift."""
        index = self._index_open_sub(lambda r: (r["entity"], r["ref_norm"]))
        found = 0
        for gl_id in sorted(self.open_gl):
            g = self.gl.loc[gl_id]
            if not g["ref_norm"]:
                continue
            candidates = [s for s in index.get((g["entity"], g["ref_norm"]), []) if s in self.open_sub]
            best = None
            for sub_id in candidates:
                s = self.sub.loc[sub_id]
                if not self.config.amounts_agree(float(g["amount"]), float(s["amount"])):
                    continue
                diff = abs(float(g["amount"]) - float(s["amount"]))
                if best is None or diff < best[1]:
                    best = (sub_id, diff)
            if best is None:
                continue
            sub_id, diff = best
            exact = self.config.is_exact_amount(float(g["amount"]), float(self.sub.loc[sub_id]["amount"]))
            match_type = REFERENCE if exact else FX_VARIANCE
            note = "" if exact else f"amount differs by {diff:,.2f} within tolerance"
            self._record(self._pair(gl_id, [sub_id], match_type, 0.95 if exact else 0.88, note))
            found += 1
        return found

    # -- pass 3: amount + date window -----------------------------------------

    def pass_timing(self) -> int:
        """Amount agrees exactly; dates differ within the window. Timing difference."""
        index = self._index_open_sub(lambda r: (r["entity"], round(float(r["amount"]), 2)))
        found = 0
        for gl_id in sorted(self.open_gl):
            g = self.gl.loc[gl_id]
            candidates = [s for s in index.get((g["entity"], round(float(g["amount"]), 2)), []) if s in self.open_sub]
            best = None
            for sub_id in candidates:
                days = self._days_between(g["txn_date"], self.sub.loc[sub_id]["txn_date"])
                if days is None or days > self.config.date_window_days:
                    continue
                if best is None or days < best[1]:
                    best = (sub_id, days)
            if best is None:
                continue
            sub_id, days = best
            self._record(
                self._pair(gl_id, [sub_id], TIMING, 0.80, f"dates differ by {days} day(s)")
            )
            found += 1
        return found

    # -- pass 4: partial / split settlement -----------------------------------

    def pass_partial_split(self) -> int:
        """One GL posting settled across several sub-ledger items.

        Grouped by reference first (cheap and near-certain), then by exhaustive
        subset search within the same entity, bounded by ``max_split_items``.
        """
        found = 0

        # 4a: same reference, several sub-ledger lines summing to the GL amount.
        by_ref = defaultdict(list)
        for sub_id in self.open_sub:
            s = self.sub.loc[sub_id]
            if s["ref_norm"]:
                by_ref[(s["entity"], s["ref_norm"])].append(sub_id)

        for gl_id in sorted(self.open_gl):
            g = self.gl.loc[gl_id]
            group = [s for s in by_ref.get((g["entity"], g["ref_norm"]), []) if s in self.open_sub]
            if len(group) < 2:
                continue
            total = round(sum(float(self.sub.loc[s]["amount"]) for s in group), 2)
            if self.config.amounts_agree(float(g["amount"]), total):
                self._record(
                    self._pair(gl_id, group, PARTIAL_SPLIT, 0.85, f"settled across {len(group)} items")
                )
                found += 1

        # 4b: no usable reference -- search small subsets by amount within entity.
        remaining_by_entity = defaultdict(list)
        for sub_id in self.open_sub:
            remaining_by_entity[self.sub.loc[sub_id]["entity"]].append(sub_id)

        for gl_id in sorted(self.open_gl):
            g = self.gl.loc[gl_id]
            if g["ref_norm"]:
                continue
            pool = [s for s in remaining_by_entity.get(g["entity"], []) if s in self.open_sub]
            if not 2 <= len(pool) <= 20:  # exhaustive search only where it is cheap
                continue
            target = float(g["amount"])
            hit = None
            for size in range(2, min(self.config.max_split_items, len(pool)) + 1):
                for combo in combinations(pool, size):
                    total = round(sum(float(self.sub.loc[s]["amount"]) for s in combo), 2)
                    if self.config.amounts_agree(target, total):
                        hit = list(combo)
                        break
                if hit:
                    break
            if hit:
                self._record(
                    self._pair(gl_id, hit, PARTIAL_SPLIT, 0.70, f"amount-only split across {len(hit)} items")
                )
                found += 1

        return found

    # -- pass 5: fuzzy reference ----------------------------------------------

    def pass_fuzzy(self) -> int:
        """Normalised references are similar and amounts agree. Last resort."""
        found = 0
        by_entity = defaultdict(list)
        for sub_id in self.open_sub:
            by_entity[self.sub.loc[sub_id]["entity"]].append(sub_id)

        for gl_id in sorted(self.open_gl):
            g = self.gl.loc[gl_id]
            if not g["ref_norm"]:
                continue
            best = None
            for sub_id in by_entity.get(g["entity"], []):
                if sub_id not in self.open_sub:
                    continue
                s = self.sub.loc[sub_id]
                if not s["ref_norm"]:
                    continue
                if not self.config.amounts_agree(float(g["amount"]), float(s["amount"])):
                    continue
                ratio = SequenceMatcher(None, g["ref_norm"], s["ref_norm"]).ratio()
                if ratio >= self.config.fuzzy_threshold and (best is None or ratio > best[1]):
                    best = (sub_id, ratio)
            if best is None:
                continue
            sub_id, ratio = best
            self._record(
                self._pair(
                    gl_id, [sub_id], FUZZY, round(ratio, 3),
                    f"reference similarity {ratio:.0%}: {g['reference']!r} vs {self.sub.loc[sub_id]['reference']!r}",
                )
            )
            found += 1
        return found

    # -- classification of what is left ---------------------------------------

    def classify_remainder(self) -> list[Exception_]:
        """Everything unmatched, categorised rather than merely listed."""
        exceptions: list[Exception_] = []

        # Signature of everything already matched, to spot duplicate postings.
        matched_sub_signatures = {
            (
                self.sub.loc[s]["entity"],
                self.sub.loc[s]["ref_norm"],
                round(float(self.sub.loc[s]["amount"]), 2),
            )
            for m in self.matches
            for s in m.sub_ids
        }

        for sub_id in sorted(self.open_sub):
            s = self.sub.loc[sub_id]
            signature = (s["entity"], s["ref_norm"], round(float(s["amount"]), 2))
            is_dup = bool(s["ref_norm"]) and signature in matched_sub_signatures
            exceptions.append(
                Exception_(
                    exception_type=DUPLICATE if is_dup else SUBLEDGER_ONLY,
                    line_id=sub_id,
                    source="SUB",
                    entity=str(s["entity"]),
                    reference=str(s["reference"]),
                    amount=float(s["amount"]),
                    txn_date=s["txn_date"],
                    description=str(s["description"]),
                    note="identical to an already-matched sub-ledger item" if is_dup else "no GL counterpart",
                )
            )

        for gl_id in sorted(self.open_gl):
            g = self.gl.loc[gl_id]
            exceptions.append(
                Exception_(
                    exception_type=GL_ONLY,
                    line_id=gl_id,
                    source="GL",
                    entity=str(g["entity"]),
                    reference=str(g["reference"]),
                    amount=float(g["amount"]),
                    txn_date=g["txn_date"],
                    description=str(g["description"]),
                    note="no sub-ledger counterpart",
                )
            )

        return exceptions

    # -- orchestration ---------------------------------------------------------

    def run(self, verbose: bool = True) -> ReconResult:
        passes = [
            ("1. exact (ref + amount + date)", self.pass_exact),
            ("2. reference + tolerant amount", self.pass_reference),
            ("3. amount + date window", self.pass_timing),
            ("4. partial / split settlement", self.pass_partial_split),
            ("5. fuzzy reference", self.pass_fuzzy),
        ]
        for label, fn in passes:
            count = fn()
            if verbose:
                print(f"  {label:<34} matched {count:>4}   (GL open: {len(self.open_gl)}, SUB open: {len(self.open_sub)})")

        return ReconResult(
            matches=self.matches,
            exceptions=self.classify_remainder(),
            gl_total=round(float(self.gl["amount"].sum()), 2),
            sub_total=round(float(self.sub["amount"].sum()), 2),
            gl_count=len(self.gl),
            sub_count=len(self.sub),
        )
