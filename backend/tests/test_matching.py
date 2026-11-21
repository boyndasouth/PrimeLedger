"""
test_matching.py — unit and integration tests for the matching agent.

Hand-made fixture cases:
  1. exact         — same amount/currency/date/payee
  2. fuzzy         — same amount, slightly different payee, date off by 3 days
  3. many_to_one   — 3 bank rows summing to 1 GL row
  4. one_to_many   — 1 bank row summing to 2 GL rows

Sample-data case:
  5. precision ≥ 0.95 against labels.json
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.agents.matching import match, MatchResult
from app.models import BankTxn, GLEntry, Match, Run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def _run(db) -> str:
    """Insert a minimal Run row and return its id."""
    rid = str(uuid.uuid4())
    run = Run(
        id=rid,
        period_start="2025-01-01",
        period_end="2025-01-31",
        status="running",
    )
    db.add(run)
    db.flush()
    return rid


def _bank(db, run_id, *, source_id, date, payee, amount, currency="USD") -> BankTxn:
    b = BankTxn(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_txn_id=source_id,
        date=date,
        description_raw=payee,
        payee_normalized=payee.upper(),
        amount=Decimal(str(amount)),
        currency=currency,
        amount_usd=Decimal(str(amount)),
    )
    db.add(b)
    db.flush()
    return b


def _gl(db, run_id, *, source_id, date, payee, amount, currency="USD") -> GLEntry:
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
    )
    db.add(g)
    db.flush()
    return g


# ---------------------------------------------------------------------------
# Case 1 — Exact match
# ---------------------------------------------------------------------------

def test_exact_match(db):
    run_id = _run(db)
    b = _bank(db, run_id, source_id="B1", date="2025-01-10", payee="STRIPE", amount="-500.00")
    g = _gl(db, run_id, source_id="G1", date="2025-01-10", payee="STRIPE", amount="-500.00")

    result = match(db, run_id)

    assert result.matched == 1
    assert result.auto_accepted == 1
    assert result.unmatched_bank == 0
    assert result.unmatched_gl == 0

    m: Match = db.query(Match).first()
    assert m.match_type == "exact"
    assert float(m.confidence) == pytest.approx(0.99)
    assert m.status == "auto_accepted"
    assert b.id in m.bank_ids
    assert g.id in m.gl_ids
    assert m.reasoning is not None and "500" in m.reasoning


# ---------------------------------------------------------------------------
# Case 2 — Fuzzy 1:1 match
# ---------------------------------------------------------------------------

def test_fuzzy_match(db):
    run_id = _run(db)
    # token_set_ratio("DATADOG INC", "DATADOG MONITOR") ≈ 78, so ≥ 70 fuzzy threshold
    b = _bank(db, run_id, source_id="B2", date="2025-01-10", payee="DATADOG INC", amount="-300.00")
    g = _gl(db, run_id, source_id="G2", date="2025-01-13", payee="DATADOG MONITOR", amount="-300.00")

    result = match(db, run_id)

    assert result.matched == 1
    m: Match = db.query(Match).first()
    # fuzzy match — confidence between 0.60 and 0.99
    assert 0.60 <= float(m.confidence) < 0.99
    assert m.match_type == "fuzzy"
    assert m.status in ("auto_accepted", "needs_review")
    assert b.id in m.bank_ids
    assert g.id in m.gl_ids


# ---------------------------------------------------------------------------
# Case 3 — Many-to-one (3 bank rows → 1 GL row)
# ---------------------------------------------------------------------------

def test_many_to_one_split(db):
    run_id = _run(db)
    # GL total = -900; three bank parts summing to -900
    b1 = _bank(db, run_id, source_id="B3a", date="2025-01-05", payee="SALESFORCE", amount="-300.00")
    b2 = _bank(db, run_id, source_id="B3b", date="2025-01-06", payee="SALESFORCE", amount="-350.00")
    b3 = _bank(db, run_id, source_id="B3c", date="2025-01-07", payee="SALESFORCE", amount="-250.00")
    g = _gl(db, run_id, source_id="G3", date="2025-01-06", payee="SALESFORCE", amount="-900.00")

    result = match(db, run_id)

    assert result.matched == 1
    m: Match = db.query(Match).first()
    assert m.match_type == "many_to_one"
    assert float(m.confidence) >= 0.60
    assert set(m.bank_ids) == {b1.id, b2.id, b3.id}
    assert m.gl_ids == [g.id]


# ---------------------------------------------------------------------------
# Case 4 — One-to-many (1 bank row → 2 GL rows)
# ---------------------------------------------------------------------------

def test_one_to_many_combined(db):
    run_id = _run(db)
    # Bank total = -1000; two GL entries summing to -1000
    b = _bank(db, run_id, source_id="B4", date="2025-01-15", payee="HUBSPOT", amount="-1000.00")
    g1 = _gl(db, run_id, source_id="G4a", date="2025-01-15", payee="HUBSPOT", amount="-600.00")
    g2 = _gl(db, run_id, source_id="G4b", date="2025-01-16", payee="HUBSPOT", amount="-400.00")

    result = match(db, run_id)

    assert result.matched == 1
    m: Match = db.query(Match).first()
    assert m.match_type == "one_to_many"
    assert m.bank_ids == [b.id]
    assert set(m.gl_ids) == {g1.id, g2.id}


# ---------------------------------------------------------------------------
# Case 5 — Each record matched at most once
# ---------------------------------------------------------------------------

def test_no_double_match(db):
    """A single GL row must not be claimed by two different bank rows."""
    run_id = _run(db)
    # Two bank rows with the same amount/payee; only one GL counterpart
    b1 = _bank(db, run_id, source_id="B5a", date="2025-01-20", payee="ZOOM", amount="-200.00")
    b2 = _bank(db, run_id, source_id="B5b", date="2025-01-20", payee="ZOOM", amount="-200.00")
    _gl(db, run_id, source_id="G5", date="2025-01-20", payee="ZOOM", amount="-200.00")

    result = match(db, run_id)

    assert result.matched == 1
    # One bank row is unmatched
    assert result.unmatched_bank == 1


# ---------------------------------------------------------------------------
# Case 6 — Below NEEDS_REVIEW threshold stays unmatched
# ---------------------------------------------------------------------------

def test_below_threshold_unmatched(db):
    """Very different payees with same amount should not produce a match."""
    run_id = _run(db)
    _bank(db, run_id, source_id="B6", date="2025-01-01", payee="ACME CORP", amount="-100.00")
    _gl(db, run_id, source_id="G6", date="2025-01-01", payee="GLOBEX LTD", amount="-100.00")

    result = match(db, run_id)

    # Confidence will be below 0.60 (poor payee similarity)
    assert result.unmatched_bank == 1 or result.unmatched_gl == 1


# ---------------------------------------------------------------------------
# Case 7 — Sample data precision ≥ 0.95 against labels.json
# ---------------------------------------------------------------------------

SAMPLES = Path(__file__).parent.parent / "data" / "samples"


def _ingest_samples(db, run_id: str) -> None:
    """Ingest the committed sample CSVs directly (bypassing the ingestion agent
    to keep this test self-contained in terms of dependency)."""
    from app.agents.ingestion import ingest as _ingest
    bank_csv = (SAMPLES / "bank.csv").read_bytes()
    gl_csv = (SAMPLES / "gl.csv").read_bytes()
    _ingest(db, run_id, bank_csv, gl_csv)


def test_sample_precision(db):
    """Precision against labels.json must be ≥ 0.95."""
    run_id = _run(db)
    _ingest_samples(db, run_id)

    result: MatchResult = match(db, run_id)

    # Load labels
    labels = json.loads((SAMPLES / "labels.json").read_text())
    true_pairs = labels["pairs"]  # list of {bank_ids, gl_ids, type}

    # Build a lookup: source_txn_id -> db UUID for bank, source_entry_id -> db UUID for GL
    bank_rows = db.query(BankTxn).filter(BankTxn.run_id == run_id).all()
    gl_rows = db.query(GLEntry).filter(GLEntry.run_id == run_id).all()
    bank_src_to_id = {b.source_txn_id: b.id for b in bank_rows}
    gl_src_to_id = {g.source_entry_id: g.id for g in gl_rows}

    # Build set of frozensets (bank_uuid_set, gl_uuid_set) from labels
    true_set: set[tuple[frozenset, frozenset]] = set()
    for pair in true_pairs:
        b_uuids = frozenset(bank_src_to_id[sid] for sid in pair["bank_ids"] if sid in bank_src_to_id)
        g_uuids = frozenset(gl_src_to_id[sid] for sid in pair["gl_ids"] if sid in gl_src_to_id)
        if b_uuids and g_uuids:
            true_set.add((b_uuids, g_uuids))

    # Build set from actual matches stored in DB
    matches = db.query(Match).filter(Match.run_id == run_id).all()
    predicted_set: set[tuple[frozenset, frozenset]] = set()
    for m in matches:
        predicted_set.add((frozenset(m.bank_ids), frozenset(m.gl_ids)))

    true_positives = len(predicted_set & true_set)
    total_predicted = len(predicted_set)

    precision = true_positives / total_predicted if total_predicted > 0 else 0.0

    assert total_predicted > 0, "No matches produced at all"
    assert precision >= 0.95, (
        f"Precision {precision:.3f} < 0.95 "
        f"(TP={true_positives}, predicted={total_predicted})"
    )
