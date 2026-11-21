import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Integer,
    JSON,
    Numeric,
    String,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, Session

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    period_start: Mapped[str] = mapped_column(String)
    period_end: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|running|done|failed
    bank_count: Mapped[int] = mapped_column(Integer, default=0)
    gl_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BankTxn(Base):
    __tablename__ = "bank_txns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"))
    source_txn_id: Mapped[str] = mapped_column(String)
    date: Mapped[str] = mapped_column(String)
    description_raw: Mapped[str] = mapped_column(String)
    payee_normalized: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[object] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String)
    amount_usd: Mapped[object] = mapped_column(Numeric(14, 2))


class GLEntry(Base):
    __tablename__ = "gl_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"))
    source_entry_id: Mapped[str] = mapped_column(String)
    date: Mapped[str] = mapped_column(String)
    account: Mapped[str] = mapped_column(String)
    payee_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    payee_normalized: Mapped[str | None] = mapped_column(String, nullable=True)
    memo: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[object] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String)
    amount_usd: Mapped[object] = mapped_column(Numeric(14, 2))
    reference: Mapped[str | None] = mapped_column(String, nullable=True)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"))
    match_type: Mapped[str] = mapped_column(String)  # exact|fuzzy|one_to_many|many_to_one
    confidence: Mapped[object] = mapped_column(Numeric(14, 2))
    bank_ids: Mapped[list] = mapped_column(JSON)
    gl_ids: Mapped[list] = mapped_column(JSON)
    reasoning: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="needs_review")  # auto_accepted|needs_review|approved|rejected


class Exception(Base):
    __tablename__ = "exceptions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"))
    side: Mapped[str] = mapped_column(String)  # bank|gl
    record_ids: Mapped[list] = mapped_column(JSON)
    cause: Mapped[str] = mapped_column(String)  # timing|bank_fee|duplicate|fx_variance|missing_invoice|other
    explanation: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_confidence: Mapped[object | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")  # open|approved|edited|rejected


class JournalDraft(Base):
    __tablename__ = "journal_drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    exception_id: Mapped[str] = mapped_column(String, ForeignKey("exceptions.id"))
    lines: Mapped[list] = mapped_column(JSON)  # [{account, debit, credit, memo}]
    drafted_by: Mapped[str] = mapped_column(String, default="resolution_agent")
    final_lines: Mapped[list | None] = mapped_column(JSON, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[str | None] = mapped_column(String, nullable=True)  # approve|edit|reject
    decision_note: Mapped[str | None] = mapped_column(String, nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)  # preparer|approver


class AuditLog(Base):
    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    entity_type: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String)
    hash: Mapped[str] = mapped_column(String)


def seed_users(db: Session) -> None:
    """Create 3 seeded users if they don't exist: one preparer, two approvers."""
    if db.query(User).count() > 0:
        return
    users = [
        User(id="user-preparer-1", name="Alice Preparer", role="preparer"),
        User(id="user-approver-1", name="Bob Approver", role="approver"),
        User(id="user-approver-2", name="Carol Approver", role="approver"),
    ]
    db.add_all(users)
    db.commit()
