"""
test_ingestion.py — normalize_payee, missing column error, sample CSV row counts.
"""
import io
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.agents.ingestion import normalize_payee, ingest, IngestResult


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


def _run_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# normalize_payee tests
# ---------------------------------------------------------------------------

class TestNormalizePayee:
    def test_amzn_alias(self):
        assert normalize_payee("AMZN MKTP US*2K4") == "AMAZON US"

    def test_amzn_simple(self):
        assert normalize_payee("AMZN") == "AMAZON"

    def test_uppercase(self):
        assert normalize_payee("stripe payments") == "STRIPE PAYMENTS"

    def test_star_replaced(self):
        assert normalize_payee("SLACK*MONTHLY") == "SLACK MONTHLY"

    def test_pos_noise_stripped(self):
        assert normalize_payee("POS STARBUCKS") == "STARBUCKS"

    def test_ach_noise_stripped(self):
        assert normalize_payee("ACH TRANSFER PAYPAL") == "TRANSFER PAYPAL"

    def test_debit_noise_stripped(self):
        result = normalize_payee("DEBIT CARD AMAZON")
        assert "DEBIT" not in result
        assert "AMAZON" in result

    def test_trailing_id_stripped(self):
        # "GOOGLE*GSUITE" with trailing ID after star
        result = normalize_payee("GOOGLE*GSUITE AB1234")
        assert "AB1234" not in result
        assert "GOOGLE" in result

    def test_empty_string(self):
        assert normalize_payee("") == ""

    def test_whitespace_collapsed(self):
        assert normalize_payee("ADOBE   SYSTEMS") == "ADOBE SYSTEMS"

    def test_walmart_alias(self):
        assert normalize_payee("WMT SUPERCENTER") == "WALMART SUPERCENTER"

    def test_paypal_alias(self):
        assert normalize_payee("PYPL *PAYMENT") == "PAYPAL PAYMENT"


# ---------------------------------------------------------------------------
# Missing column error tests
# ---------------------------------------------------------------------------

def _make_bank_csv(**overrides) -> bytes:
    cols = {
        "txn_id": ["T001"],
        "date": ["2025-01-01"],
        "description": ["TEST"],
        "amount": ["-100.00"],
        "currency": ["USD"],
    }
    cols.update(overrides)
    import pandas as pd
    buf = io.BytesIO()
    pd.DataFrame(cols).to_csv(buf, index=False)
    return buf.getvalue()


def _make_gl_csv(**overrides) -> bytes:
    cols = {
        "entry_id": ["G001"],
        "date": ["2025-01-01"],
        "account": ["1000"],
        "payee": ["Vendor"],
        "memo": ["Test"],
        "amount": ["-100.00"],
        "currency": ["USD"],
    }
    cols.update(overrides)
    import pandas as pd
    buf = io.BytesIO()
    pd.DataFrame(cols).to_csv(buf, index=False)
    return buf.getvalue()


def test_missing_bank_column_raises(db):
    import pandas as pd
    # Remove 'amount' column
    bank = io.BytesIO()
    pd.DataFrame({
        "txn_id": ["T1"], "date": ["2025-01-01"],
        "description": ["X"], "currency": ["USD"],
    }).to_csv(bank, index=False)
    bank.seek(0)

    with pytest.raises(ValueError, match="amount"):
        ingest(db, _run_id(), bank.getvalue(), _make_gl_csv())


def test_missing_gl_column_raises(db):
    import pandas as pd
    gl = io.BytesIO()
    pd.DataFrame({
        "entry_id": ["G1"], "date": ["2025-01-01"],
        "account": ["1000"], "memo": ["x"],
        "amount": ["-10"], "currency": ["USD"],
        # 'payee' is missing
    }).to_csv(gl, index=False)
    gl.seek(0)

    with pytest.raises(ValueError, match="payee"):
        ingest(db, _run_id(), _make_bank_csv(), gl.getvalue())


def test_missing_multiple_bank_columns_message_lists_all(db):
    import pandas as pd
    bank = io.BytesIO()
    pd.DataFrame({"txn_id": ["T1"]}).to_csv(bank, index=False)
    bank.seek(0)

    with pytest.raises(ValueError) as exc_info:
        ingest(db, _run_id(), bank.getvalue(), _make_gl_csv())
    msg = str(exc_info.value)
    # At least two missing columns mentioned
    assert "date" in msg or "description" in msg or "amount" in msg


# ---------------------------------------------------------------------------
# Sample CSV ingestion row counts
# ---------------------------------------------------------------------------

def _read_sample(filename: str) -> bytes:
    from pathlib import Path
    path = Path(__file__).parent.parent / "data" / "samples" / filename
    return path.read_bytes()


def test_sample_ingest_row_counts(db):
    bank_csv = _read_sample("bank.csv")
    gl_csv = _read_sample("gl.csv")
    result: IngestResult = ingest(db, _run_id(), bank_csv, gl_csv)

    assert result.bank_count == 296
    assert result.gl_count == 279


def test_sample_ingest_audit_entry(db):
    """ingest() writes exactly one audit row with action 'ingest.completed'."""
    from app.models import AuditLog

    bank_csv = _read_sample("bank.csv")
    gl_csv = _read_sample("gl.csv")
    ingest(db, _run_id(), bank_csv, gl_csv)

    rows = db.query(AuditLog).filter(AuditLog.action == "ingest.completed").all()
    assert len(rows) == 1
    assert rows[0].payload["bank_count"] == 296
    assert rows[0].payload["gl_count"] == 279


def test_sample_ingest_persists_rows(db):
    """BankTxn and GLEntry rows are actually committed to the DB."""
    from app.models import BankTxn, GLEntry

    bank_csv = _read_sample("bank.csv")
    gl_csv = _read_sample("gl.csv")
    ingest(db, _run_id(), bank_csv, gl_csv)

    assert db.query(BankTxn).count() == 296
    assert db.query(GLEntry).count() == 279


def test_fx_conversion_eur(db):
    """EUR amounts are converted to USD using FX_RATES."""
    from app.models import BankTxn
    from decimal import Decimal

    bank = io.BytesIO()
    import pandas as pd
    pd.DataFrame({
        "txn_id": ["T1"], "date": ["2025-01-01"],
        "description": ["VENDOR"], "amount": ["100.00"], "currency": ["EUR"],
    }).to_csv(bank, index=False)

    result = ingest(db, _run_id(), bank.getvalue(), _make_gl_csv())
    txn = db.query(BankTxn).first()
    # 100 EUR * 1.08 = 108.00 USD
    assert txn.amount_usd == Decimal("108.00")
