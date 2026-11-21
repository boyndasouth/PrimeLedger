"""Investigation Agent — classify each unmatched record as an exception.

For every unmatched bank or GL record:
  1. Run five cheap heuristics to collect hints.
  2. Find the 3 nearest candidates on the other side (by amount then date).
  3. Call the LLM with the masked record + candidates + hints.
  4. Validate the JSON response with a Pydantic model.
  5. On failure retry once; if still failing fall back to the top heuristic.
  6. Write an Exception row + one audit log entry per record.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.audit import log as audit_log
from app.llm.masking import mask
from app.llm.prompts import PROMPTS
from app.llm.watsonx_client import generate_json
from app.models import BankTxn, Exception as ExceptionModel, GLEntry, Match

# ---------------------------------------------------------------------------
# Allowed cause values (spec §6.3)
# ---------------------------------------------------------------------------

ALLOWED_CAUSES = frozenset(
    {"timing", "bank_fee", "duplicate", "fx_variance", "missing_invoice", "other"}
)

# ---------------------------------------------------------------------------
# LLM response schema
# ---------------------------------------------------------------------------

CauseType = Literal["timing", "bank_fee", "duplicate", "fx_variance", "missing_invoice", "other"]


class LLMExceptionResponse(BaseModel):
    cause: CauseType
    explanation: str
    confidence: float

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


# ---------------------------------------------------------------------------
# Heuristics — pure functions, no DB side-effects
# ---------------------------------------------------------------------------

_FEE_RE = re.compile(r"\b(FEE|CHG|CHARGE|SERVICE)\b", re.IGNORECASE)
_FX_THRESHOLD = Decimal("0.03")


@dataclass
class HeuristicResult:
    cause: str
    rationale: str


def _h_bank_fee(record: BankTxn | GLEntry, side: str) -> HeuristicResult | None:
    """bank_fee: bank-side, small negative amount, description contains FEE/CHG/SERVICE."""
    if side != "bank":
        return None
    b = record  # type: BankTxn
    desc = getattr(b, "description_raw", "") or ""
    if Decimal(str(b.amount)) < 0 and abs(Decimal(str(b.amount))) < Decimal("50") and _FEE_RE.search(desc):
        return HeuristicResult("bank_fee", f"Small negative amount {b.amount} with fee keyword in description.")
    return None


def _h_duplicate(
    record: BankTxn | GLEntry,
    side: str,
    same_side: list[BankTxn] | list[GLEntry],
) -> HeuristicResult | None:
    """duplicate: same amount + payee + date within 2 days on the same side."""
    rec_date = date.fromisoformat(record.date)
    rec_amount = Decimal(str(record.amount))
    rec_payee = (getattr(record, "payee_normalized", "") or "").upper()

    for other in same_side:
        if other.id == record.id:
            continue
        if Decimal(str(other.amount)) != rec_amount:
            continue
        if abs((date.fromisoformat(other.date) - rec_date).days) > 2:
            continue
        other_payee = (getattr(other, "payee_normalized", "") or "").upper()
        if other_payee != rec_payee:
            continue
        return HeuristicResult(
            "duplicate",
            f"Same amount {record.amount} and payee '{rec_payee}' found within 2 days (ID {other.id}).",
        )
    return None


def _h_timing(
    record: BankTxn | GLEntry,
    side: str,
    opposite_all: list[BankTxn] | list[GLEntry],
    period_start: str,
    period_end: str,
) -> HeuristicResult | None:
    """timing: a matching counterpart exists just outside the period or date window."""
    rec_amount = Decimal(str(record.amount))
    ps = date.fromisoformat(period_start)
    pe = date.fromisoformat(period_end)

    for other in opposite_all:
        if Decimal(str(other.amount)) != rec_amount:
            continue
        od = date.fromisoformat(other.date)
        # Outside the reconciliation period but within 10 days of its boundary
        outside_before = od < ps and (ps - od).days <= 10
        outside_after = od > pe and (od - pe).days <= 10
        if outside_before or outside_after:
            return HeuristicResult(
                "timing",
                f"Counterpart ID {other.id} with same amount {other.amount} is just outside the period ({other.date}).",
            )
    return None


def _h_fx_variance(
    record: BankTxn | GLEntry,
    side: str,
    opposite_all: list[BankTxn] | list[GLEntry],
) -> HeuristicResult | None:
    """fx_variance: counterpart differs by < 3% and currencies differ."""
    rec_amount = abs(Decimal(str(record.amount)))
    rec_currency = record.currency

    for other in opposite_all:
        if other.currency == rec_currency:
            continue
        other_amount = abs(Decimal(str(other.amount)))
        if other_amount == 0:
            continue
        variance = abs(rec_amount - other_amount) / other_amount
        if variance < _FX_THRESHOLD:
            return HeuristicResult(
                "fx_variance",
                f"Counterpart ID {other.id} differs by {float(variance):.1%} with currency {other.currency} vs {rec_currency}.",
            )
    return None


def _h_missing_invoice(
    record: BankTxn | GLEntry,
    side: str,
    hints_so_far: list[HeuristicResult],
    opposite_all: list[BankTxn] | list[GLEntry],
) -> HeuristicResult | None:
    """missing_invoice: bank deposit/withdrawal with no GL counterpart and no other signal."""
    if side != "bank" or hints_so_far:
        return None
    rec_amount = Decimal(str(record.amount))
    for other in opposite_all:
        if Decimal(str(other.amount)) == rec_amount:
            return None  # a rough counterpart exists
    return HeuristicResult(
        "missing_invoice",
        f"Bank record {record.id} has no matching GL counterpart and no other classification signal.",
    )


def run_heuristics(
    record: BankTxn | GLEntry,
    side: str,
    same_side: list,
    opposite_all: list,
    period_start: str,
    period_end: str,
) -> list[HeuristicResult]:
    """Run all five heuristics and return a list of matches (may be empty)."""
    hints: list[HeuristicResult] = []

    h = _h_bank_fee(record, side)
    if h:
        hints.append(h)

    h = _h_duplicate(record, side, same_side)
    if h:
        hints.append(h)

    h = _h_timing(record, side, opposite_all, period_start, period_end)
    if h:
        hints.append(h)

    h = _h_fx_variance(record, side, opposite_all)
    if h:
        hints.append(h)

    h = _h_missing_invoice(record, side, hints, opposite_all)
    if h:
        hints.append(h)

    return hints


# ---------------------------------------------------------------------------
# Candidate search — 3 nearest on the other side by amount then date
# ---------------------------------------------------------------------------

def _nearest_candidates(
    record: BankTxn | GLEntry,
    opposite_all: list,
    n: int = 3,
) -> list:
    rec_amount = Decimal(str(record.amount))
    rec_date = date.fromisoformat(record.date)

    def _key(other):
        amount_diff = abs(Decimal(str(other.amount)) - rec_amount)
        date_diff = abs((date.fromisoformat(other.date) - rec_date).days)
        return (amount_diff, date_diff)

    return sorted(opposite_all, key=_key)[:n]


# ---------------------------------------------------------------------------
# LLM call with retry + fallback
# ---------------------------------------------------------------------------

def _call_llm(record: BankTxn | GLEntry, candidates: list, hints: list[HeuristicResult]) -> LLMExceptionResponse:
    """Call the LLM and validate; retry once; fall back to top heuristic on failure."""
    prompt = PROMPTS["investigate"].format(
        record_id=record.id,
        record_date=record.date,
        record_amount=str(record.amount),
        record_currency=record.currency,
        record_payee=mask(getattr(record, "payee_normalized", "") or getattr(record, "payee_raw", "") or ""),
        record_description=mask(getattr(record, "description_raw", "") or ""),
        candidates=_format_candidates(candidates),
        hints=_format_hints(hints),
    )

    schema_hint = '{"cause": "<cause>", "explanation": "<string>", "confidence": <float 0-1>}'

    for _attempt in range(2):
        try:
            raw = generate_json(prompt, schema_hint)
            return LLMExceptionResponse(**raw)
        except (ValidationError, KeyError, TypeError, ValueError):
            pass

    # Fallback
    if hints:
        top = hints[0]
        return LLMExceptionResponse(
            cause=top.cause,  # type: ignore[arg-type]
            explanation="Heuristic classification; model unavailable.",
            confidence=0.5,
        )
    return LLMExceptionResponse(
        cause="other",
        explanation="Heuristic classification; model unavailable.",
        confidence=0.5,
    )


def _format_candidates(candidates: list) -> str:
    lines = []
    for c in candidates:
        payee = getattr(c, "payee_normalized", "") or ""
        lines.append(
            f"  - ID={c.id} date={c.date} amount={c.amount} currency={c.currency} payee={mask(payee)}"
        )
    return "\n".join(lines) if lines else "  (none)"


def _format_hints(hints: list[HeuristicResult]) -> str:
    if not hints:
        return "  (no heuristic signals)"
    return "\n".join(f"  - [{h.cause}] {h.rationale}" for h in hints)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def investigate(db: Session, run_id: str) -> list[ExceptionModel]:
    """Classify all unmatched records for *run_id*; return created Exception rows."""
    # Determine which records are matched
    matches = db.query(Match).filter(Match.run_id == run_id).all()
    matched_bank_ids: set[str] = set()
    matched_gl_ids: set[str] = set()
    for m in matches:
        matched_bank_ids.update(m.bank_ids)
        matched_gl_ids.update(m.gl_ids)

    all_bank: list[BankTxn] = db.query(BankTxn).filter(BankTxn.run_id == run_id).all()
    all_gl: list[GLEntry] = db.query(GLEntry).filter(GLEntry.run_id == run_id).all()

    unmatched_bank = [b for b in all_bank if b.id not in matched_bank_ids]
    unmatched_gl = [g for g in all_gl if g.id not in matched_gl_ids]

    # Retrieve period for timing heuristic
    from app.models import Run as RunModel
    run = db.query(RunModel).filter(RunModel.id == run_id).one()
    period_start = run.period_start
    period_end = run.period_end

    created: list[ExceptionModel] = []

    for record in unmatched_bank:
        exc = _process_record(
            db, run_id, record, "bank",
            same_side=all_bank,
            opposite_all=all_gl,
            period_start=period_start,
            period_end=period_end,
        )
        created.append(exc)

    for record in unmatched_gl:
        exc = _process_record(
            db, run_id, record, "gl",
            same_side=all_gl,
            opposite_all=all_bank,
            period_start=period_start,
            period_end=period_end,
        )
        created.append(exc)

    db.commit()
    return created


def _process_record(
    db: Session,
    run_id: str,
    record: BankTxn | GLEntry,
    side: str,
    same_side: list,
    opposite_all: list,
    period_start: str,
    period_end: str,
) -> ExceptionModel:
    hints = run_heuristics(record, side, same_side, opposite_all, period_start, period_end)
    candidates = _nearest_candidates(record, opposite_all)
    llm_resp = _call_llm(record, candidates, hints)

    exc = ExceptionModel(
        run_id=run_id,
        side=side,
        record_ids=[record.id],
        cause=llm_resp.cause,
        explanation=llm_resp.explanation,
        llm_confidence=Decimal(str(round(llm_resp.confidence, 4))),
        status="open",
    )
    db.add(exc)
    db.flush()

    audit_log.append(
        db=db,
        actor="investigation_agent",
        action="exception.created",
        entity_type="exception",
        entity_id=exc.id,
        payload={
            "run_id": run_id,
            "side": side,
            "record_ids": [record.id],
            "cause": llm_resp.cause,
            "confidence": str(llm_resp.confidence),
            "hints": [{"cause": h.cause, "rationale": h.rationale} for h in hints],
        },
    )
    return exc
