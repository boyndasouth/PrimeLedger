"""
test_investigation.py — unit tests for the Investigation Agent.

Tests:
  1. bank_fee heuristic fires on matching fixture.
  2. duplicate heuristic fires on matching fixture.
  3. timing heuristic fires on matching fixture.
  4. fx_variance heuristic fires on matching fixture.
  5. missing_invoice heuristic fires on matching fixture.
  6. investigate() creates Exception rows and audit log entries.
  7. Invalid LLM output triggers the fallback (confidence=0.5, top heuristic).
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
from app.models import AuditLog, BankTxn, Exception as ExceptionModel, GLEntry, Run
from app.agents.investigation import (
    HeuristicResult,
    _h_bank_fee,
    _h_duplicate,
    _h_fx_variance,
    _h_missing_invoice,
    _h_timing,
    investigate,
    run_heuristics,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _force_mock_llm(monkeypatch):
    """Force LLM_MODE=mock for every test so no real watsonx calls are made."""
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


def _bank(
    db,
    run_id,
    *,
    source_id="B1",
    date="2025-01-10",
    payee="TEST PAYEE",
    amount="-25.00",
    currency="USD",
    description="SERVICE FEE",
) -> BankTxn:
    b = BankTxn(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_txn_id=source_id,
        date=date,
        description_raw=description,
        payee_normalized=payee.upper(),
        amount=Decimal(str(amount)),
        currency=currency,
        amount_usd=Decimal(str(amount)),
    )
    db.add(b)
    db.flush()
    return b


def _gl(
    db,
    run_id,
    *,
    source_id="G1",
    date="2025-01-10",
    payee="TEST PAYEE",
    amount="-25.00",
    currency="USD",
) -> GLEntry:
    g = GLEntry(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_entry_id=source_id,
        date=date,
        account="6000",
        payee_raw=payee,
        payee_normalized=payee.upper(),
        memo=None,
        amount=Decimal(str(amount)),
        currency=currency,
        amount_usd=Decimal(str(amount)),
        reference=None,
    )
    db.add(g)
    db.flush()
    return g


# ---------------------------------------------------------------------------
# Heuristic unit tests (pure function tests — no DB side-effects)
# ---------------------------------------------------------------------------

def test_bank_fee_fires():
    """bank_fee fires on bank side, small negative, description has FEE."""
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="SERVICE FEE",
        payee_normalized="BANKOFX", amount=Decimal("-12.50"),
        currency="USD", amount_usd=Decimal("-12.50"),
    )
    result = _h_bank_fee(b, "bank")
    assert result is not None
    assert result.cause == "bank_fee"


def test_bank_fee_does_not_fire_on_gl_side():
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="SERVICE FEE",
        payee_normalized="BANKOFX", amount=Decimal("-12.50"),
        currency="USD", amount_usd=Decimal("-12.50"),
    )
    result = _h_bank_fee(b, "gl")
    assert result is None


def test_bank_fee_does_not_fire_large_amount():
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="SERVICE FEE",
        payee_normalized="BIGBANK", amount=Decimal("-500.00"),
        currency="USD", amount_usd=Decimal("-500.00"),
    )
    result = _h_bank_fee(b, "bank")
    assert result is None


def test_duplicate_fires():
    """duplicate fires when same amount+payee within 2 days on same side."""
    b1 = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="STRIPE",
        payee_normalized="STRIPE", amount=Decimal("-100.00"),
        currency="USD", amount_usd=Decimal("-100.00"),
    )
    b2 = BankTxn(
        id="b2", run_id="r1", source_txn_id="s2",
        date="2025-01-11", description_raw="STRIPE",
        payee_normalized="STRIPE", amount=Decimal("-100.00"),
        currency="USD", amount_usd=Decimal("-100.00"),
    )
    result = _h_duplicate(b1, "bank", [b1, b2])
    assert result is not None
    assert result.cause == "duplicate"


def test_duplicate_does_not_fire_different_payee():
    b1 = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="STRIPE",
        payee_normalized="STRIPE", amount=Decimal("-100.00"),
        currency="USD", amount_usd=Decimal("-100.00"),
    )
    b2 = BankTxn(
        id="b2", run_id="r1", source_txn_id="s2",
        date="2025-01-10", description_raw="PAYPAL",
        payee_normalized="PAYPAL", amount=Decimal("-100.00"),
        currency="USD", amount_usd=Decimal("-100.00"),
    )
    result = _h_duplicate(b1, "bank", [b1, b2])
    assert result is None


def test_timing_fires():
    """timing fires when counterpart is just outside the period."""
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="VENDOR",
        payee_normalized="VENDOR", amount=Decimal("-200.00"),
        currency="USD", amount_usd=Decimal("-200.00"),
    )
    # GL entry dated 5 days after period end (Jan 31)
    g_outside = GLEntry(
        id="g1", run_id="r1", source_entry_id="sg1",
        date="2025-02-04", account="6000",
        payee_raw="VENDOR", payee_normalized="VENDOR",
        memo=None, amount=Decimal("-200.00"),
        currency="USD", amount_usd=Decimal("-200.00"),
        reference=None,
    )
    result = _h_timing(b, "bank", [g_outside], "2025-01-01", "2025-01-31")
    assert result is not None
    assert result.cause == "timing"


def test_timing_does_not_fire_inside_period():
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="VENDOR",
        payee_normalized="VENDOR", amount=Decimal("-200.00"),
        currency="USD", amount_usd=Decimal("-200.00"),
    )
    g_inside = GLEntry(
        id="g1", run_id="r1", source_entry_id="sg1",
        date="2025-01-20", account="6000",
        payee_raw="VENDOR", payee_normalized="VENDOR",
        memo=None, amount=Decimal("-200.00"),
        currency="USD", amount_usd=Decimal("-200.00"),
        reference=None,
    )
    result = _h_timing(b, "bank", [g_inside], "2025-01-01", "2025-01-31")
    assert result is None


def test_fx_variance_fires():
    """fx_variance fires when counterpart differs by < 3% and currencies differ."""
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="INTL",
        payee_normalized="INTL", amount=Decimal("-100.00"),
        currency="USD", amount_usd=Decimal("-100.00"),
    )
    g_fx = GLEntry(
        id="g1", run_id="r1", source_entry_id="sg1",
        date="2025-01-10", account="6000",
        payee_raw="INTL", payee_normalized="INTL",
        memo=None, amount=Decimal("-101.50"),  # 1.5% variance
        currency="EUR", amount_usd=Decimal("-101.50"),
        reference=None,
    )
    result = _h_fx_variance(b, "bank", [g_fx])
    assert result is not None
    assert result.cause == "fx_variance"


def test_fx_variance_does_not_fire_same_currency():
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="INTL",
        payee_normalized="INTL", amount=Decimal("-100.00"),
        currency="USD", amount_usd=Decimal("-100.00"),
    )
    g_same = GLEntry(
        id="g1", run_id="r1", source_entry_id="sg1",
        date="2025-01-10", account="6000",
        payee_raw="INTL", payee_normalized="INTL",
        memo=None, amount=Decimal("-101.50"),
        currency="USD", amount_usd=Decimal("-101.50"),
        reference=None,
    )
    result = _h_fx_variance(b, "bank", [g_same])
    assert result is None


def test_missing_invoice_fires():
    """missing_invoice fires: bank record, no hints, no GL counterpart."""
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="MYSTERY",
        payee_normalized="MYSTERY", amount=Decimal("-500.00"),
        currency="USD", amount_usd=Decimal("-500.00"),
    )
    # GL with completely different amount
    g_other = GLEntry(
        id="g1", run_id="r1", source_entry_id="sg1",
        date="2025-01-10", account="6000",
        payee_raw="OTHER", payee_normalized="OTHER",
        memo=None, amount=Decimal("-999.00"),
        currency="USD", amount_usd=Decimal("-999.00"),
        reference=None,
    )
    result = _h_missing_invoice(b, "bank", [], [g_other])
    assert result is not None
    assert result.cause == "missing_invoice"


def test_missing_invoice_does_not_fire_when_other_hints_present():
    b = BankTxn(
        id="b1", run_id="r1", source_txn_id="s1",
        date="2025-01-10", description_raw="MYSTERY",
        payee_normalized="MYSTERY", amount=Decimal("-500.00"),
        currency="USD", amount_usd=Decimal("-500.00"),
    )
    existing_hint = HeuristicResult("bank_fee", "already classified")
    result = _h_missing_invoice(b, "bank", [existing_hint], [])
    assert result is None


# ---------------------------------------------------------------------------
# Integration tests with in-memory DB
# ---------------------------------------------------------------------------

def test_investigate_creates_exceptions(db):
    """investigate() should create one Exception and one AuditLog per unmatched record."""
    run_id = _run(db)
    # One unmatched bank record (no GL counterpart)
    _bank(db, run_id, source_id="B1", description="SERVICE FEE", amount="-10.00")

    result = investigate(db, run_id)

    assert len(result) == 1
    exc = result[0]
    assert exc.side == "bank"
    assert exc.cause in {"bank_fee", "timing", "duplicate", "fx_variance", "missing_invoice", "other"}
    assert exc.status == "open"
    assert exc.llm_confidence is not None

    # Audit log entry must exist
    logs = db.query(AuditLog).filter(AuditLog.action == "exception.created").all()
    assert len(logs) == 1
    assert logs[0].entity_id == exc.id


def test_investigate_creates_exception_for_gl(db):
    """An unmatched GL record also gets an exception."""
    run_id = _run(db)
    _gl(db, run_id, source_id="G1", amount="-300.00")

    result = investigate(db, run_id)

    assert len(result) == 1
    assert result[0].side == "gl"


def test_investigate_skips_matched_records(db):
    """Matched records must not produce exceptions."""
    from app.models import Match

    run_id = _run(db)
    b = _bank(db, run_id, source_id="B1", description="STRIPE", amount="-500.00")
    g = _gl(db, run_id, source_id="G1", amount="-500.00")

    # Manually insert a match (simulates matching agent)
    m = Match(
        id=str(uuid.uuid4()),
        run_id=run_id,
        match_type="exact",
        confidence=Decimal("0.99"),
        bank_ids=[b.id],
        gl_ids=[g.id],
        reasoning="manual",
        status="auto_accepted",
    )
    db.add(m)
    db.flush()

    result = investigate(db, run_id)

    assert result == []


def test_investigate_fallback_on_invalid_llm_output(db):
    """When generate_json returns invalid JSON, the fallback kicks in."""
    run_id = _run(db)
    # A bank fee record so the top heuristic is bank_fee
    _bank(
        db, run_id, source_id="B1",
        description="MONTHLY SERVICE FEE", amount="-5.00",
        payee="BIGBANK",
    )

    # Patch generate_json to return something that fails Pydantic validation
    with patch("app.agents.investigation.generate_json", return_value={"bad_key": "bad_value"}):
        result = investigate(db, run_id)

    assert len(result) == 1
    exc = result[0]
    # Fallback should produce confidence 0.5
    assert float(exc.llm_confidence) == pytest.approx(0.5)
    assert exc.explanation == "Heuristic classification; model unavailable."
    # top heuristic for this record should be bank_fee
    assert exc.cause == "bank_fee"


def test_investigate_multiple_unmatched(db):
    """All unmatched records on both sides get exceptions."""
    run_id = _run(db)
    _bank(db, run_id, source_id="B1", description="WIRE TRANSFER", amount="-1000.00")
    _bank(db, run_id, source_id="B2", description="SERVICE FEE", amount="-20.00")
    _gl(db, run_id, source_id="G1", amount="-500.00")

    result = investigate(db, run_id)

    assert len(result) == 3
    sides = [e.side for e in result]
    assert sides.count("bank") == 2
    assert sides.count("gl") == 1

    # Each exception has its own audit log entry
    logs = db.query(AuditLog).filter(AuditLog.action == "exception.created").all()
    assert len(logs) == 3
