"""Matching Agent — deterministic reconciliation in four stages.

Stage order (each record matched at most once):
  1. Exact        : same amount+currency, date ±1 day, payee similarity ≥ 90 → 0.99
  2. Fuzzy 1:1    : same amount, date ±5 days, token_set_ratio ≥ 70
                    confidence = 0.6 * (payee_sim/100) + 0.4 * date_closeness
  3. Many-to-one /
     one-to-many  : subsets ≤ 3 records, date ±5 days, sum exact to the cent → 0.85
                    (lowered by 0.05 if payees differ)
  4. LLM tiebreak : TODO – returns None (skipped for now)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Any

from rapidfuzz.fuzz import token_set_ratio
from sqlalchemy.orm import Session

from app.audit import log as audit_log
from app.config import AUTO_ACCEPT, NEEDS_REVIEW
from app.models import BankTxn, GLEntry, Match

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    matched: int = 0
    auto_accepted: int = 0
    needs_review: int = 0
    unmatched_bank: int = 0
    unmatched_gl: int = 0
    match_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TODO hook – LLM tiebreak (optional, section 6.2 step 4)
# ---------------------------------------------------------------------------

def llm_tiebreak(
    bank_records: list[BankTxn],
    gl_records: list[GLEntry],
    *,
    confidence: float,
) -> None:
    """Optional LLM tiebreak for borderline candidates (confidence 0.55–0.75).

    TODO: call watsonx_client.generate_json() with masked prompt, return
    adjusted confidence or None to leave unchanged. Cap at 20 calls per run.
    """
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATE_WINDOW = 5   # days used for fuzzy + subset search
_EXACT_WINDOW = 1  # days used for exact match
_MIN_PAYEE_EXACT = 90.0
_MIN_PAYEE_FUZZY = 70.0
_SUBSET_MAX = 3
_SUBSET_BASE_CONF = Decimal("0.85")
_SUBSET_PAYEE_PENALTY = Decimal("0.05")


def _iso_to_date(s: str) -> date:
    return date.fromisoformat(s)


def _date_closeness(d1: date, d2: date, window: int) -> float:
    """Linear closeness: 1.0 when same day, 0.0 at |delta| == window."""
    delta = abs((d1 - d2).days)
    if delta > window:
        return 0.0
    return 1.0 - delta / window


def _payee_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return token_set_ratio(a, b)


def _status(confidence: Decimal) -> str:
    f = float(confidence)
    if f >= AUTO_ACCEPT:
        return "auto_accepted"
    if f >= NEEDS_REVIEW:
        return "needs_review"
    return "unmatched"


def _store_match(
    db: Session,
    run_id: str,
    match_type: str,
    confidence: Decimal,
    bank_records: list[BankTxn],
    gl_records: list[GLEntry],
    reasoning: str,
) -> Match | None:
    """Persist a match row; returns None if confidence is below NEEDS_REVIEW."""
    status = _status(confidence)
    if status == "unmatched":
        return None

    m = Match(
        id=str(uuid.uuid4()),
        run_id=run_id,
        match_type=match_type,
        confidence=confidence,
        bank_ids=[b.id for b in bank_records],
        gl_ids=[g.id for g in gl_records],
        reasoning=reasoning,
        status=status,
    )
    db.add(m)
    db.flush()

    audit_log.append(
        db=db,
        actor="matching_agent",
        action="match.created",
        entity_type="match",
        entity_id=m.id,
        payload={
            "run_id": run_id,
            "match_type": match_type,
            "confidence": str(confidence),
            "bank_ids": m.bank_ids,
            "gl_ids": m.gl_ids,
            "reasoning": reasoning,
            "status": status,
        },
    )
    return m


# ---------------------------------------------------------------------------
# Stage 1 — Exact
# ---------------------------------------------------------------------------

def _stage_exact(
    db: Session,
    run_id: str,
    bank_pool: dict[str, BankTxn],
    gl_pool: dict[str, GLEntry],
    result: MatchResult,
) -> None:
    matched_bank: set[str] = set()
    matched_gl: set[str] = set()

    for b in list(bank_pool.values()):
        bd = _iso_to_date(b.date)
        for g in list(gl_pool.values()):
            if g.id in matched_gl:
                continue
            if b.amount != g.amount:
                continue
            if b.currency != g.currency:
                continue
            gd = _iso_to_date(g.date)
            if abs((bd - gd).days) > _EXACT_WINDOW:
                continue
            sim = _payee_sim(b.payee_normalized or "", g.payee_normalized or "")
            if sim < _MIN_PAYEE_EXACT:
                continue
            # Hit — build reasoning
            days_diff = abs((bd - gd).days)
            reasoning = (
                f"Same amount {b.amount} {b.currency}, "
                f"date +{days_diff}d, payee similarity {sim:.0f}"
            )
            m = _store_match(db, run_id, "exact", Decimal("0.99"), [b], [g], reasoning)
            if m:
                matched_bank.add(b.id)
                matched_gl.add(g.id)
                result.matched += 1
                result.auto_accepted += 1
                result.match_ids.append(m.id)
            break  # each bank txn matched at most once

    for bid in matched_bank:
        bank_pool.pop(bid, None)
    for gid in matched_gl:
        gl_pool.pop(gid, None)


# ---------------------------------------------------------------------------
# Stage 2 — Fuzzy 1:1
# ---------------------------------------------------------------------------

def _stage_fuzzy(
    db: Session,
    run_id: str,
    bank_pool: dict[str, BankTxn],
    gl_pool: dict[str, GLEntry],
    result: MatchResult,
) -> None:
    matched_bank: set[str] = set()
    matched_gl: set[str] = set()

    for b in list(bank_pool.values()):
        bd = _iso_to_date(b.date)
        best_conf: Decimal | None = None
        best_g: GLEntry | None = None
        best_reasoning = ""

        for g in list(gl_pool.values()):
            if g.id in matched_gl:
                continue
            if b.amount != g.amount:
                continue
            gd = _iso_to_date(g.date)
            days_diff = abs((bd - gd).days)
            if days_diff > _DATE_WINDOW:
                continue
            sim = _payee_sim(b.payee_normalized or "", g.payee_normalized or "")
            if sim < _MIN_PAYEE_FUZZY:
                continue
            closeness = _date_closeness(bd, gd, _DATE_WINDOW)
            conf = Decimal(str(round(0.6 * (sim / 100) + 0.4 * closeness, 4)))
            if best_conf is None or conf > best_conf:
                best_conf = conf
                best_g = g
                best_reasoning = (
                    f"Same amount {b.amount} {b.currency}, "
                    f"date +{days_diff}d, payee similarity {sim:.0f}"
                )

        if best_g is not None and best_conf is not None:
            m = _store_match(db, run_id, "fuzzy", best_conf, [b], [best_g], best_reasoning)
            if m:
                matched_bank.add(b.id)
                matched_gl.add(best_g.id)
                result.matched += 1
                if m.status == "auto_accepted":
                    result.auto_accepted += 1
                else:
                    result.needs_review += 1
                result.match_ids.append(m.id)

    for bid in matched_bank:
        bank_pool.pop(bid, None)
    for gid in matched_gl:
        gl_pool.pop(gid, None)


# ---------------------------------------------------------------------------
# Stage 3 — Many-to-one / one-to-many subsets
# ---------------------------------------------------------------------------

def _subset_search(
    singles: list[Any],
    multis: list[Any],
    match_type: str,
    db: Session,
    run_id: str,
    result: MatchResult,
) -> tuple[set[str], set[str]]:
    """Search for subsets of *multis* (≤ 3) summing to each *single*.

    Returns (matched_single_ids, matched_multi_ids).
    """
    matched_singles: set[str] = set()
    matched_multis: set[str] = set()

    for single in singles:
        if single.id in matched_singles:
            continue
        target: Decimal = single.amount
        sd = _iso_to_date(single.date)
        # Bucket candidates within ±5 days
        candidates = [
            m for m in multis
            if m.id not in matched_multis
            and abs((_iso_to_date(m.date) - sd).days) <= _DATE_WINDOW
        ]
        found = False
        for size in range(2, _SUBSET_MAX + 1):
            if found:
                break
            for combo in combinations(candidates, size):
                total = sum(c.amount for c in combo)
                if total != target:
                    continue
                # Same currency check
                currencies = {c.currency for c in combo}
                if len(currencies) > 1 or single.currency not in currencies:
                    continue
                # Confidence: start at 0.85, lower if payees differ
                payees_match = all(
                    _payee_sim(
                        getattr(single, "payee_normalized") or "",
                        c.payee_normalized or "",
                    ) >= _MIN_PAYEE_FUZZY
                    for c in combo
                )
                conf = _SUBSET_BASE_CONF
                if not payees_match:
                    conf -= _SUBSET_PAYEE_PENALTY

                dates_str = ", ".join(c.date for c in combo)
                combo_amounts = "+".join(str(c.amount) for c in combo)
                reasoning = (
                    f"{match_type}: {combo_amounts}={target} {single.currency}, "
                    f"dates [{dates_str}] vs {single.date}"
                )

                # Determine which are bank/GL
                if match_type == "many_to_one":
                    bank_recs = list(combo)
                    gl_recs = [single]
                else:
                    bank_recs = [single]
                    gl_recs = list(combo)

                m = _store_match(db, run_id, match_type, conf, bank_recs, gl_recs, reasoning)
                if m:
                    matched_singles.add(single.id)
                    for c in combo:
                        matched_multis.add(c.id)
                    result.matched += 1
                    if m.status == "auto_accepted":
                        result.auto_accepted += 1
                    else:
                        result.needs_review += 1
                    result.match_ids.append(m.id)
                found = True
                break

    return matched_singles, matched_multis


def _stage_subset(
    db: Session,
    run_id: str,
    bank_pool: dict[str, BankTxn],
    gl_pool: dict[str, GLEntry],
    result: MatchResult,
) -> None:
    bank_list = list(bank_pool.values())
    gl_list = list(gl_pool.values())

    # many-to-one: N bank rows sum to 1 GL row
    mb, mg = _subset_search(gl_list, bank_list, "many_to_one", db, run_id, result)
    for bid in mb:
        bank_pool.pop(bid, None)
    for gid in mg:
        gl_pool.pop(gid, None)

    # one-to-many: 1 bank row sums to N GL rows (using updated pools)
    bank_list = list(bank_pool.values())
    gl_list = list(gl_pool.values())
    mb2, mg2 = _subset_search(bank_list, gl_list, "one_to_many", db, run_id, result)
    for bid in mb2:
        bank_pool.pop(bid, None)
    for gid in mg2:
        gl_pool.pop(gid, None)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def match(db: Session, run_id: str) -> MatchResult:
    """Run all matching stages for *run_id*; returns a MatchResult summary."""
    bank_rows = db.query(BankTxn).filter(BankTxn.run_id == run_id).all()
    gl_rows = db.query(GLEntry).filter(GLEntry.run_id == run_id).all()

    bank_pool: dict[str, BankTxn] = {b.id: b for b in bank_rows}
    gl_pool: dict[str, GLEntry] = {g.id: g for g in gl_rows}

    result = MatchResult()

    _stage_exact(db, run_id, bank_pool, gl_pool, result)
    _stage_fuzzy(db, run_id, bank_pool, gl_pool, result)
    _stage_subset(db, run_id, bank_pool, gl_pool, result)

    result.unmatched_bank = len(bank_pool)
    result.unmatched_gl = len(gl_pool)

    db.commit()
    return result
