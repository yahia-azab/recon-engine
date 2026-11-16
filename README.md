# recon-engine

**Automated balance sheet reconciliation for finance teams — matches GL to sub-ledger, classifies exceptions, produces an audit trail.**

Reconciliation is the most automatable task in accounting and the one most teams still do by hand in Excel.
This engine takes a general ledger extract and a sub-ledger extract and does what an accountant does — but in
seconds, deterministically, with a defensible audit trail.

It is built around how reconciliation actually fails in practice: timing differences, partial settlements, FX
movement, duplicated postings and reference-format mismatches. Naive amount-matching handles none of these.

---

## What it does

| Stage | Behaviour |
|---|---|
| **Ingest** | Loads GL and sub-ledger extracts (Excel/CSV), normalises references, dates and currencies |
| **Match** | Multi-pass matching: exact → reference → amount+window → partial/split → fuzzy |
| **Classify** | Every unmatched item is categorised, not just flagged: timing, FX variance, partial, duplicate, genuine break |
| **Report** | Excel workbook with matched pairs, exception register by category, and a full audit trail |

## Why multi-pass matching

A single matching rule either misses real matches or creates false ones. The engine runs passes in order of
confidence and removes matched items from the pool before the next pass, so high-confidence matches are never
stolen by looser rules later.

1. **Exact** — reference + amount + date agree
2. **Reference** — reference agrees, amount within tolerance (catches rounding and minor FX)
3. **Amount + window** — amount agrees, date within N days (catches timing differences)
4. **Partial / split** — one GL posting against several sub-ledger items, or vice versa
5. **Fuzzy reference** — normalised reference similarity above threshold (catches format drift between systems)

Anything surviving all five passes is a genuine break and goes to the exception register.

## Quick start

```bash
pip install -r requirements.txt

# generate a realistic synthetic dataset (no real data needed, ever)
python scripts/generate_sample_data.py --out data/

# run a reconciliation
python -m recon_engine.cli --gl data/gl_extract.xlsx --subledger data/subledger_extract.xlsx --out output/
```

The sample generator deliberately injects every failure mode the engine is designed to find — timing
differences, FX drift, split settlements, duplicates and true breaks — so you can see it work on data whose
answers are known.

## Verified results

Because the generator records every discrepancy it injects, the engine's work can be checked exactly — not
just "it produced a number", but *did it find what we know is there, and did it invent anything that isn't*.

```
python scripts/verify_against_key.py
```

Run on 618 GL lines against 676 sub-ledger lines containing 170 injected discrepancies:

| Injected | Count | Engine result |
|---|---|---|
| Timing differences | 40 | matched |
| Reference format drift | 30 | matched |
| FX / rounding variance | 25 | matched, classified `fx_variance` |
| Partial / split settlement | 20 | matched, classified `partial_split` |
| Sub-ledger only (true break) | 22 | raised as exception |
| GL only (true break) | 18 | raised as exception |
| Duplicate postings | 15 | raised as exception |

```
matched     : 600/618 GL lines (97.1%)
exceptions  : 55  (18 gl_only + 22 subledger_only + 15 duplicate)

ALL CHECKS PASSED
False positives (invented breaks): 0
```

Every genuine break was found, every explainable difference was matched and labelled with its cause, and no
break was invented. The false-positive count is the number that matters: a missed match gets reviewed by a
human, a false match does not.

## Project status

Core matching engine, synthetic data generator, verification harness and Excel reporting are working.
Next: unit test suite, web interface, and scheduled/unattended runs.

## Licence

MIT — free to use, modify and build on.
