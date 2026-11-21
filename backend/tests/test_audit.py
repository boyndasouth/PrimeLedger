"""
test_audit.py — hash chain verifies; tampering is detected.
Uses an in-memory SQLite database.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.audit import log as audit_log


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


def test_empty_chain_is_valid(db):
    result = audit_log.verify(db)
    assert result == {"valid": True, "broken_at_seq": None}


def test_chain_verifies_after_appends(db):
    audit_log.append(db, "agent", "run.created", "run", "run-1", {"note": "first"})
    audit_log.append(db, "user-1", "journal.approved", "journal_draft", "jd-1", {"decision": "approve"})
    audit_log.append(db, "agent", "ingest.completed", "run", "run-1", {"bank_count": 10})

    result = audit_log.verify(db)
    assert result["valid"] is True
    assert result["broken_at_seq"] is None


def test_tampered_payload_breaks_chain(db):
    r1 = audit_log.append(db, "agent", "run.created", "run", "run-1", {"note": "first"})
    r2 = audit_log.append(db, "agent", "ingest.completed", "run", "run-1", {"bank_count": 10})
    r3 = audit_log.append(db, "agent", "match.created", "match", "m-1", {"confidence": 0.99})

    # Directly mutate row 2's payload in the DB (simulates tampering)
    from app.models import AuditLog
    tampered = db.query(AuditLog).filter(AuditLog.seq == r2.seq).one()
    tampered.payload = {"bank_count": 999}  # changed value
    db.commit()

    result = audit_log.verify(db)
    assert result["valid"] is False
    assert result["broken_at_seq"] == r2.seq


def test_tampered_hash_breaks_chain(db):
    r1 = audit_log.append(db, "agent", "run.created", "run", "run-1", {})
    r2 = audit_log.append(db, "agent", "ingest.completed", "run", "run-1", {})

    from app.models import AuditLog
    row = db.query(AuditLog).filter(AuditLog.seq == r1.seq).one()
    row.hash = "deadbeef" * 8  # corrupt the stored hash
    db.commit()

    result = audit_log.verify(db)
    assert result["valid"] is False
    assert result["broken_at_seq"] == r1.seq
