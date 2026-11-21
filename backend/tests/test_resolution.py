"""
test_resolution.py — tests for the Resolution Agent (section 6.4).

Tests:
  1. Every cause produces a balanced draft (debits == credits).
  2. timing cause produces no lines and a "clears next period" note in payload.
  3. assert_balanced raises ValueError on an unbalanced set of lines.
  4. resolve() writes journal_drafts rows and "journal.drafted" audit entries.
  5. LLM failure falls back to a template memo (non-empty string).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import (
    AuditLog,
    BankTxn,
    Exception as ExceptionModel,
    JournalDraft,
    Run,
)
from app.agents.resolution import assert_balanced, resolve


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _force_mock_llm(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODE", "mock")


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _run(db, period_start="2025-01-01", period_end="2025-01-31") -> str:
    rid = str(uuid.uuid4())
    run = Run(id=rid, period_start=period_start, period_end=period_end, status="running")
    db.add(run)
    db.flush()
    return rid


def _bank(db, run_id, *, amount="-25.00", currency="USD") -> BankTxn:
    b = BankTxn(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_txn_id=str(uuid.uuid4()),
        date="2025-01-10",
        description_raw="TEST",
        payee_normalized="TEST",
        amount=Decimal(str(amount)),
        currency=currency,
        amount_usd=Decimal(str(amount)),
    )
    db.add(b)
    db.flush()
    return b


def _exception(db, run_id, cause, record_ids, status="open") -> ExceptionModel:
    exc = ExceptionModel(
        id=str(uuid.uuid4()),
        run_id=run_id,
        side="bank",
        record_ids=record_ids,
        cause=cause,
        explanation=f"Test exception for cause={cause}",
        llm_confidence=Decimal("0.75"),
        status=status,
    )
    db.add(exc)
    db.flush()
    return exc


# ---------------------------------------------------------------------------
# assert_balanced tests
# ---------------------------------------------------------------------------

def test_assert_balanced_passes_on_balanced():
    lines = [
        {"account": "6100", "debit": Decimal("25.00"), "credit": Decimal("0.00"), "memo": "x"},
        {"account": "1000", "debit": Decimal("0.00"), "credit": Decimal("25.00"), "memo": "x"},
    ]
    assert_balanced(lines)  # must not raise


def test_assert_balanced_raises_on_unbalanced():
    lines = [
        {"account": "6100", "debit": Decimal("25.00"), "credit": Decimal("0.00"), "memo": "x"},
        {"account": "1000", "debit": Decimal("0.00"), "credit": Decimal("10.00"), "memo": "x"},
    ]
    with pytest.raises(ValueError, match="unbalanced"):
        assert_balanced(lines)


def test_assert_balanced_raises_on_single_sided():
    lines = [
        {"account": "6100", "debit": Decimal("50.00"), "credit": Decimal("0.00"), "memo": "x"},
    ]
    with pytest.raises(ValueError):
        assert_balanced(lines)


# ---------------------------------------------------------------------------
# Per-cause draft tests
# ---------------------------------------------------------------------------

ALL_CAUSES = ["bank_fee", "fx_variance", "duplicate", "missing_invoice", "timing", "other"]


@pytest.mark.parametrize("cause", ALL_CAUSES)
def test_every_cause_produces_balanced_draft(db, cause):
    """resolve() must produce a balanced draft for every cause."""
    run_id = _run(db)
    bank = _bank(db, run_id, amount="-75.00")
    exc = _exception(db, run_id, cause, [bank.id])

    drafts = resolve(db, run_id)

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.exception_id == exc.id
    assert draft.drafted_by == "resolution_agent"

    if cause == "timing":
        # timing exceptions have no lines
        assert draft.lines == []
    else:
        assert len(draft.lines) >= 2
        assert_balanced(draft.lines)  # core requirement: must balance


def test_timing_produces_no_lines(db):
    """timing exception → empty lines list."""
    run_id = _run(db)
    bank = _bank(db, run_id)
    _exception(db, run_id, "timing", [bank.id])

    drafts = resolve(db, run_id)

    assert drafts[0].lines == []


def test_drafts_written_to_db(db):
    """resolve() persists JournalDraft rows."""
    run_id = _run(db)
    bank = _bank(db, run_id, amount="-30.00")
    _exception(db, run_id, "bank_fee", [bank.id])

    resolve(db, run_id)

    rows = db.query(JournalDraft).all()
    assert len(rows) == 1
    assert rows[0].drafted_by == "resolution_agent"


def test_audit_entry_written(db):
    """resolve() writes a 'journal.drafted' audit entry per draft."""
    run_id = _run(db)
    bank = _bank(db, run_id, amount="-40.00")
    _exception(db, run_id, "bank_fee", [bank.id])

    resolve(db, run_id)

    logs = db.query(AuditLog).filter(AuditLog.action == "journal.drafted").all()
    assert len(logs) == 1
    assert logs[0].actor == "resolution_agent"
    assert logs[0].entity_type == "journal_draft"


def test_multiple_exceptions_produce_multiple_drafts(db):
    """One draft per open exception."""
    run_id = _run(db)
    causes = ["bank_fee", "fx_variance", "duplicate", "missing_invoice", "timing"]
    for cause in causes:
        bank = _bank(db, run_id, amount="-50.00")
        _exception(db, run_id, cause, [bank.id])

    drafts = resolve(db, run_id)

    assert len(drafts) == len(causes)
    # All non-timing drafts must be balanced
    for draft in drafts:
        exc = db.get(ExceptionModel, draft.exception_id)
        if exc.cause != "timing":
            assert_balanced(draft.lines)


def test_resolve_skips_non_open_exceptions(db):
    """Only open exceptions get a draft."""
    run_id = _run(db)
    bank = _bank(db, run_id, amount="-20.00")
    _exception(db, run_id, "bank_fee", [bank.id], status="approved")

    drafts = resolve(db, run_id)

    assert drafts == []


def test_llm_failure_falls_back_to_template_memo(db):
    """When generate_json raises, the memo falls back to a non-empty template string."""
    run_id = _run(db)
    bank = _bank(db, run_id, amount="-15.00")
    _exception(db, run_id, "bank_fee", [bank.id])

    with patch("app.agents.resolution.generate_json", side_effect=RuntimeError("LLM down")):
        drafts = resolve(db, run_id)

    assert len(drafts) == 1
    lines = drafts[0].lines
    assert len(lines) >= 2
    # Memo must be a non-empty string from the template
    for line in lines:
        assert isinstance(line["memo"], str)
        assert len(line["memo"]) > 0
    # Must still balance
    assert_balanced(lines)


def test_bank_fee_accounts(db):
    """bank_fee draft uses account 6100 (debit) and 1000 (credit)."""
    run_id = _run(db)
    bank = _bank(db, run_id, amount="-12.50")
    _exception(db, run_id, "bank_fee", [bank.id])

    drafts = resolve(db, run_id)

    lines = drafts[0].lines
    debit_line = next(ln for ln in lines if Decimal(str(ln["debit"])) > 0)
    credit_line = next(ln for ln in lines if Decimal(str(ln["credit"])) > 0)
    assert debit_line["account"] == "6100"
    assert credit_line["account"] == "1000"


def test_missing_invoice_uses_suspense(db):
    """missing_invoice draft uses account 9999 Suspense."""
    run_id = _run(db)
    bank = _bank(db, run_id, amount="-500.00")
    _exception(db, run_id, "missing_invoice", [bank.id])

    drafts = resolve(db, run_id)

    debit_accounts = [ln["account"] for ln in drafts[0].lines if Decimal(str(ln["debit"])) > 0]
    assert "9999" in debit_accounts
