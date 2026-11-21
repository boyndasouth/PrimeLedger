"""test_sod.py — segregation of duties: preparers cannot approve/edit/reject."""
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("LLM_MODE", "mock")

from app.db import Base
from app.models import (
    Exception as ExceptionModel,
    JournalDraft,
    Run,
    User,
    seed_users,
)
from app.routes.exceptions import post_decision
from app.schemas import DecisionIn


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
    seed_users(session)
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _make_exception_with_draft(db) -> tuple[str, str]:
    """Create a minimal open exception + journal draft; return (exception_id, draft_id)."""
    run = Run(
        id=str(uuid.uuid4()),
        period_start="2025-01-01",
        period_end="2025-01-31",
        status="done",
    )
    db.add(run)
    db.flush()

    exc = ExceptionModel(
        id=str(uuid.uuid4()),
        run_id=run.id,
        side="bank",
        record_ids=[],
        cause="bank_fee",
        explanation="Test exception",
        llm_confidence="0.80",
        status="open",
    )
    db.add(exc)
    db.flush()

    draft = JournalDraft(
        id=str(uuid.uuid4()),
        exception_id=exc.id,
        lines=[
            {"account": "6100", "debit": "10.00", "credit": "0.00", "memo": "fee"},
            {"account": "1000", "debit": "0.00", "credit": "10.00", "memo": "fee"},
        ],
        drafted_by="resolution_agent",
    )
    db.add(draft)
    db.commit()
    return exc.id, draft.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_preparer_cannot_approve(db):
    exc_id, _ = _make_exception_with_draft(db)
    body = DecisionIn(decision="approve", user_id="user-preparer-1")

    with pytest.raises(HTTPException) as exc_info:
        post_decision(exception_id=exc_id, body=body, db=db)

    assert exc_info.value.status_code == 403


def test_preparer_cannot_edit(db):
    exc_id, _ = _make_exception_with_draft(db)
    body = DecisionIn(
        decision="edit",
        user_id="user-preparer-1",
        final_lines=[
            {"account": "6100", "debit": "10.00", "credit": "0.00", "memo": "fee"},
            {"account": "1000", "debit": "0.00", "credit": "10.00", "memo": "fee"},
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        post_decision(exception_id=exc_id, body=body, db=db)

    assert exc_info.value.status_code == 403


def test_preparer_cannot_reject(db):
    exc_id, _ = _make_exception_with_draft(db)
    body = DecisionIn(decision="reject", user_id="user-preparer-1")

    with pytest.raises(HTTPException) as exc_info:
        post_decision(exception_id=exc_id, body=body, db=db)

    assert exc_info.value.status_code == 403


def test_approver_can_approve(db):
    exc_id, _ = _make_exception_with_draft(db)
    body = DecisionIn(decision="approve", user_id="user-approver-1")

    result = post_decision(exception_id=exc_id, body=body, db=db)

    assert result.decision == "approve"
    assert result.decided_by == "user-approver-1"


def test_approver_can_reject(db):
    exc_id, _ = _make_exception_with_draft(db)
    body = DecisionIn(decision="reject", user_id="user-approver-1")

    result = post_decision(exception_id=exc_id, body=body, db=db)

    assert result.decision == "reject"


def test_approver_edit_unbalanced_lines_rejected(db):
    exc_id, _ = _make_exception_with_draft(db)
    body = DecisionIn(
        decision="edit",
        user_id="user-approver-1",
        final_lines=[
            {"account": "6100", "debit": "10.00", "credit": "0.00", "memo": "fee"},
            {"account": "1000", "debit": "0.00", "credit": "5.00", "memo": "fee"},  # unbalanced
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        post_decision(exception_id=exc_id, body=body, db=db)

    assert exc_info.value.status_code == 422


def test_approver_edit_balanced_lines_accepted(db):
    exc_id, _ = _make_exception_with_draft(db)
    body = DecisionIn(
        decision="edit",
        user_id="user-approver-1",
        final_lines=[
            {"account": "6100", "debit": "15.00", "credit": "0.00", "memo": "edited"},
            {"account": "1000", "debit": "0.00", "credit": "15.00", "memo": "edited"},
        ],
    )

    result = post_decision(exception_id=exc_id, body=body, db=db)

    assert result.decision == "edit"


def test_decision_writes_audit_entry(db):
    exc_id, _ = _make_exception_with_draft(db)
    body = DecisionIn(decision="approve", user_id="user-approver-2")

    post_decision(exception_id=exc_id, body=body, db=db)

    from app.models import AuditLog
    rows = db.query(AuditLog).filter(AuditLog.action == "journal.approved").all()
    assert len(rows) >= 1
    assert rows[-1].actor == "user-approver-2"
