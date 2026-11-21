"""Routes: exceptions — GET /api/exceptions/{id}, POST /api/exceptions/{id}/decision."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.resolution import assert_balanced
from app.audit import log as audit_log
from app.db import get_db
from app.models import (
    BankTxn,
    GLEntry,
    Exception as ExceptionModel,
    JournalDraft,
    User,
)
from app.schemas import (
    DecisionIn,
    DecisionOut,
    ExceptionDetailOut,
    ExceptionOut,
    GLEntryOut,
    BankTxnOut,
    JournalDraftOut,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/exceptions/{id} — exception + source records + journal draft
# ---------------------------------------------------------------------------

@router.get("/exceptions/{exception_id}", response_model=ExceptionDetailOut)
def get_exception(exception_id: str, db: Session = Depends(get_db)):
    exc = db.get(ExceptionModel, exception_id)
    if exc is None:
        raise HTTPException(status_code=404, detail="Exception not found")

    # Collect source records
    record_ids: list[str] = exc.record_ids or []
    bank_records = []
    gl_records = []
    for rid in record_ids:
        b = db.get(BankTxn, rid)
        if b:
            bank_records.append(BankTxnOut.model_validate(b))
            continue
        g = db.get(GLEntry, rid)
        if g:
            gl_records.append(GLEntryOut.model_validate(g))

    # Latest journal draft
    draft = (
        db.query(JournalDraft)
        .filter(JournalDraft.exception_id == exception_id)
        .order_by(JournalDraft.id.desc())
        .first()
    )

    return ExceptionDetailOut(
        exception=ExceptionOut.model_validate(exc),
        bank_records=bank_records,
        gl_records=gl_records,
        journal_draft=JournalDraftOut.model_validate(draft) if draft else None,
    )


# ---------------------------------------------------------------------------
# POST /api/exceptions/{id}/decision — approve | edit | reject
# ---------------------------------------------------------------------------

@router.post("/exceptions/{exception_id}/decision", response_model=DecisionOut)
def post_decision(
    exception_id: str,
    body: DecisionIn,
    db: Session = Depends(get_db),
):
    exc = db.get(ExceptionModel, exception_id)
    if exc is None:
        raise HTTPException(status_code=404, detail="Exception not found")

    # Segregation of duties: preparers cannot decide
    user = db.get(User, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "preparer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Preparers cannot approve, edit, or reject journal entries.",
        )

    # Validate decision value
    if body.decision not in {"approve", "edit", "reject"}:
        raise HTTPException(status_code=422, detail="decision must be approve, edit, or reject")

    # For 'edit', final_lines must be provided and balanced
    if body.decision == "edit":
        if not body.final_lines:
            raise HTTPException(status_code=422, detail="final_lines required for edit decision")
        try:
            assert_balanced(body.final_lines)
        except ValueError as exc_bal:
            raise HTTPException(status_code=422, detail=str(exc_bal))

    # Update exception status
    exc.status = body.decision + "d" if body.decision in {"approve", "reject"} else "edited"
    decided_at = datetime.now(timezone.utc)
    db.flush()

    # Update journal draft
    draft = (
        db.query(JournalDraft)
        .filter(JournalDraft.exception_id == exception_id)
        .order_by(JournalDraft.id.desc())
        .first()
    )
    if draft:
        draft.decided_by = body.user_id
        draft.decided_at = decided_at
        draft.decision = body.decision
        draft.decision_note = body.note
        if body.decision == "edit" and body.final_lines:
            draft.final_lines = body.final_lines
        db.flush()

    db.commit()

    # Audit
    action_map = {"approve": "journal.approved", "edit": "journal.edited", "reject": "journal.rejected"}
    audit_log.append(
        db=db,
        actor=body.user_id,
        action=action_map[body.decision],
        entity_type="journal_draft",
        entity_id=draft.id if draft else exception_id,
        payload={
            "exception_id": exception_id,
            "decision": body.decision,
            "note": body.note,
            "final_lines": body.final_lines,
        },
    )

    return DecisionOut(
        exception_id=exception_id,
        decision=body.decision,
        decided_by=body.user_id,
        decided_at=decided_at,
    )
