"""Pydantic response models for all API endpoints (section 10)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

class RunSummary(BaseModel):
    id: str
    created_at: datetime
    period_start: str
    period_end: str
    status: str
    bank_count: int
    gl_count: int
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Match
# ---------------------------------------------------------------------------

class MatchOut(BaseModel):
    id: str
    run_id: str
    match_type: str
    confidence: Decimal
    bank_ids: list[str]
    gl_ids: list[str]
    reasoning: str | None
    status: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class ExceptionOut(BaseModel):
    id: str
    run_id: str
    side: str
    record_ids: list[str]
    cause: str
    explanation: str | None
    llm_confidence: Decimal | None
    status: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Bank transaction / GL entry (for exception detail)
# ---------------------------------------------------------------------------

class BankTxnOut(BaseModel):
    id: str
    source_txn_id: str
    date: str
    description_raw: str
    payee_normalized: str | None
    amount: Decimal
    currency: str
    amount_usd: Decimal

    model_config = {"from_attributes": True}


class GLEntryOut(BaseModel):
    id: str
    source_entry_id: str
    date: str
    account: str
    payee_raw: str | None
    payee_normalized: str | None
    memo: str | None
    amount: Decimal
    currency: str
    amount_usd: Decimal
    reference: str | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Journal draft
# ---------------------------------------------------------------------------

class JournalDraftOut(BaseModel):
    id: str
    exception_id: str
    lines: list[dict[str, Any]]
    drafted_by: str
    final_lines: list[dict[str, Any]] | None
    decided_by: str | None
    decided_at: datetime | None
    decision: str | None
    decision_note: str | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Exception detail (rich response for GET /api/exceptions/{id})
# ---------------------------------------------------------------------------

class ExceptionDetailOut(BaseModel):
    exception: ExceptionOut
    bank_records: list[BankTxnOut]
    gl_records: list[GLEntryOut]
    journal_draft: JournalDraftOut | None


# ---------------------------------------------------------------------------
# Decision request / response
# ---------------------------------------------------------------------------

class DecisionIn(BaseModel):
    decision: str                        # approve | edit | reject
    user_id: str
    final_lines: list[dict[str, Any]] | None = None
    note: str | None = None


class DecisionOut(BaseModel):
    exception_id: str
    decision: str
    decided_by: str
    decided_at: datetime


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class AuditLogOut(BaseModel):
    seq: int
    ts: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    model_config = {"from_attributes": True}


class VerifyOut(BaseModel):
    valid: bool
    broken_at_seq: int | None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class PipelineSeconds(BaseModel):
    ingestion: float | None = None
    matching: float | None = None
    investigation: float | None = None
    resolution: float | None = None
    total: float | None = None


class MetricsOut(BaseModel):
    run_id: str
    auto_match_rate: float
    match_precision: float | None
    match_recall: float | None
    cause_accuracy: float | None
    pipeline_seconds: PipelineSeconds
    manual_baseline_minutes: int
    audit_complete: bool
    note: str | None = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthOut(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Demo seed
# ---------------------------------------------------------------------------

class SeedOut(BaseModel):
    run_id: str
    status: str
    bank_count: int
    gl_count: int


# ---------------------------------------------------------------------------
# Orchestrate per-stage I/O (section 6 — each agent exposed individually)
# ---------------------------------------------------------------------------

class IngestIn(BaseModel):
    """Body for POST /api/runs/{id}/ingest."""
    # bank_file and gl_file are UploadFile; this schema is for doc/OpenAPI only.
    # The actual endpoint uses Form/File params; this model is used for the
    # response only.
    pass


class IngestStageOut(BaseModel):
    run_id: str
    bank_count: int
    gl_count: int
    warnings: list[str]


class MatchStageOut(BaseModel):
    run_id: str
    matched: int
    auto_accepted: int
    needs_review: int
    unmatched_bank: int
    unmatched_gl: int
    match_ids: list[str]


class InvestigateStageOut(BaseModel):
    run_id: str
    exception_count: int
    exceptions: list[ExceptionOut]


class ResolveStageOut(BaseModel):
    run_id: str
    draft_count: int
    drafts: list[JournalDraftOut]
