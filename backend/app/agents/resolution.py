"""
resolution.py — Resolution Agent (section 6.4).

resolve(db, run_id) -> list[JournalDraft]

One JournalDraft per open exception.  Account selection and amounts come from
code (chart of accounts in config.py); only the memo text is LLM-generated.
Timing exceptions get no lines and a "clears next period" note.
Every draft is written to journal_drafts and a "journal.drafted" audit entry.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.audit import log as audit_log
from app.config import CHART_OF_ACCOUNTS
from app.llm.prompts import PROMPTS
from app.llm.watsonx_client import generate_json
from app.models import Exception as ExceptionModel, JournalDraft

# ---------------------------------------------------------------------------
# Line type alias
# ---------------------------------------------------------------------------

# A journal line: {"account": str, "debit": Decimal, "credit": Decimal, "memo": str}
Line = dict[str, Any]


# ---------------------------------------------------------------------------
# assert_balanced
# ---------------------------------------------------------------------------

def assert_balanced(lines: list[Line]) -> None:
    """Raise ValueError if total debits != total credits (Decimal comparison)."""
    total_debit = sum(Decimal(str(ln["debit"])) for ln in lines)
    total_credit = sum(Decimal(str(ln["credit"])) for ln in lines)
    if total_debit != total_credit:
        raise ValueError(
            f"Journal entry is unbalanced: debits={total_debit}, credits={total_credit}"
        )


# ---------------------------------------------------------------------------
# Memo generation (LLM writes only the memo text)
# ---------------------------------------------------------------------------

def _get_memo(
    cause: str,
    explanation: str,
    amount: Decimal,
    currency: str,
    debit_account: str,
    credit_account: str,
) -> str:
    """Call LLM for a memo; fall back to a template if the call fails."""
    try:
        prompt = PROMPTS["memo"].format(
            cause=cause,
            explanation=explanation,
            amount=amount,
            currency=currency,
            debit_account=debit_account,
            credit_account=credit_account,
        )
        result = generate_json(prompt, schema_hint='{"memo": "string"}')
        memo = str(result.get("memo", "")).strip()
        if memo:
            return memo
    except Exception:  # noqa: BLE001
        pass
    # Template fallback
    return _template_memo(cause)


_TEMPLATE_MEMOS: dict[str, str] = {
    "bank_fee": "Bank service charge; no GL counterpart.",
    "fx_variance": "FX rate variance adjustment between bank and GL.",
    "duplicate": "Reversal of duplicate GL entry.",
    "missing_invoice": "Amount posted to Suspense; obtain invoice to clear.",
    "timing": "Clears next period.",
    "other": "Unclassified exception; pending manual review.",
}


def _template_memo(cause: str) -> str:
    return _TEMPLATE_MEMOS.get(cause, _TEMPLATE_MEMOS["other"])


# ---------------------------------------------------------------------------
# Per-cause line builders
# ---------------------------------------------------------------------------

def _lines_bank_fee(amount: Decimal, memo: str) -> list[Line]:
    """bank_fee → Dr 6100 Bank Fees / Cr 1000 Cash."""
    abs_amt = str(abs(amount))
    return [
        {"account": "6100", "debit": abs_amt, "credit": "0.00", "memo": memo},
        {"account": "1000", "debit": "0.00", "credit": abs_amt, "memo": memo},
    ]


def _lines_fx_variance(amount: Decimal, memo: str) -> list[Line]:
    """fx_variance → Dr/Cr 7900 FX Gain/Loss against 1000 Cash."""
    abs_amt = str(abs(amount))
    return [
        {"account": "7900", "debit": abs_amt, "credit": "0.00", "memo": memo},
        {"account": "1000", "debit": "0.00", "credit": abs_amt, "memo": memo},
    ]


def _lines_duplicate(amount: Decimal, memo: str) -> list[Line]:
    """duplicate → reverse the duplicate GL entry (Dr 2000 AP / Cr 1000 Cash)."""
    abs_amt = str(abs(amount))
    return [
        {"account": "2000", "debit": abs_amt, "credit": "0.00", "memo": memo},
        {"account": "1000", "debit": "0.00", "credit": abs_amt, "memo": memo},
    ]


def _lines_missing_invoice(amount: Decimal, memo: str) -> list[Line]:
    """missing_invoice → 9999 Suspense / 1000 Cash."""
    abs_amt = str(abs(amount))
    return [
        {"account": "9999", "debit": abs_amt, "credit": "0.00", "memo": memo},
        {"account": "1000", "debit": "0.00", "credit": abs_amt, "memo": memo},
    ]


_LINE_BUILDERS = {
    "bank_fee": _lines_bank_fee,
    "fx_variance": _lines_fx_variance,
    "duplicate": _lines_duplicate,
    "missing_invoice": _lines_missing_invoice,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(db: Session, run_id: str) -> list[JournalDraft]:
    """
    Draft a journal entry for every open exception in *run_id*.
    Returns the list of created JournalDraft rows.
    """
    exceptions: list[ExceptionModel] = (
        db.query(ExceptionModel)
        .filter(ExceptionModel.run_id == run_id, ExceptionModel.status == "open")
        .all()
    )

    drafts: list[JournalDraft] = []

    for exc in exceptions:
        cause = exc.cause
        amount = Decimal(str(exc.explanation and "0" or "0"))  # default; overridden below

        # Retrieve a representative amount from the exception's source records
        # (use the first record_id to look up bank/gl amount)
        amount = _representative_amount(db, exc)

        if cause == "timing":
            # No lines; just a note
            lines: list[Line] = []
            memo = _template_memo("timing")
        else:
            builder = _LINE_BUILDERS.get(cause, _lines_missing_invoice)
            # Determine debit/credit account names for the memo prompt
            debit_acct, credit_acct = _accounts_for_cause(cause)
            memo = _get_memo(
                cause=cause,
                explanation=exc.explanation or "",
                amount=amount,
                currency="USD",
                debit_account=f"{debit_acct} - {CHART_OF_ACCOUNTS.get(debit_acct, '')}",
                credit_account=f"{credit_acct} - {CHART_OF_ACCOUNTS.get(credit_acct, '')}",
            )
            lines = builder(amount, memo)
            assert_balanced(lines)

        draft = JournalDraft(
            exception_id=exc.id,
            lines=lines,
            drafted_by="resolution_agent",
        )
        db.add(draft)
        db.flush()

        audit_log.append(
            db=db,
            actor="resolution_agent",
            action="journal.drafted",
            entity_type="journal_draft",
            entity_id=draft.id,
            payload={
                "exception_id": exc.id,
                "cause": cause,
                "line_count": len(lines),
                "note": memo if cause == "timing" else None,
            },
        )

        drafts.append(draft)

    return drafts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _accounts_for_cause(cause: str) -> tuple[str, str]:
    """Return (debit_account_code, credit_account_code) for a cause."""
    mapping = {
        "bank_fee": ("6100", "1000"),
        "fx_variance": ("7900", "1000"),
        "duplicate": ("2000", "1000"),
        "missing_invoice": ("9999", "1000"),
        "other": ("9999", "1000"),
    }
    return mapping.get(cause, ("9999", "1000"))


def _representative_amount(db: Session, exc: ExceptionModel) -> Decimal:
    """Return the absolute amount from the exception's first source record."""
    from app.models import BankTxn, GLEntry

    record_ids: list[str] = exc.record_ids or []
    if not record_ids:
        return Decimal("0.00")

    first_id = record_ids[0]
    bank = db.get(BankTxn, first_id)
    if bank is not None:
        return Decimal(str(bank.amount))
    gl = db.get(GLEntry, first_id)
    if gl is not None:
        return Decimal(str(gl.amount))
    return Decimal("0.00")
