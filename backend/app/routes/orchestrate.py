"""Per-stage API routes for watsonx Orchestrate tool registration.

Each endpoint runs exactly one reconciliation agent against an existing run.
Prerequisite: the run must already exist (created via POST /api/runs or the
demo seed).  Call the stages in order:

  POST /api/runs/{id}/ingest      — Ingestion Agent
  POST /api/runs/{id}/match       — Matching Agent
  POST /api/runs/{id}/investigate — Investigation Agent
  POST /api/runs/{id}/resolve     — Resolution Agent

These thin wrappers expose typed, documented JSON I/O so each agent can be
registered individually as an Orchestrate skill.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.agents.ingestion import ingest
from app.agents.investigation import investigate
from app.agents.matching import match
from app.agents.resolution import resolve
from app.db import get_db
from app.models import Exception as ExceptionModel, JournalDraft, Run
from app.schemas import (
    ExceptionOut,
    IngestStageOut,
    InvestigateStageOut,
    JournalDraftOut,
    MatchStageOut,
    ResolveStageOut,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_run_or_404(run_id: str, db: Session) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/ingest
# ---------------------------------------------------------------------------

@router.post(
    "/runs/{run_id}/ingest",
    response_model=IngestStageOut,
    status_code=status.HTTP_200_OK,
    summary="Ingestion Agent — parse CSVs and load records into the run",
    tags=["orchestrate"],
)
async def run_ingest(
    run_id: str,
    bank_file: UploadFile = File(..., description="Bank statement CSV"),
    gl_file: UploadFile = File(..., description="GL export CSV"),
    db: Session = Depends(get_db),
) -> IngestStageOut:
    _get_run_or_404(run_id, db)
    bank_csv = await bank_file.read()
    gl_csv = await gl_file.read()
    result = ingest(db, run_id, bank_csv, gl_csv)
    db.commit()
    return IngestStageOut(
        run_id=run_id,
        bank_count=result.bank_count,
        gl_count=result.gl_count,
        warnings=result.warnings,
    )


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/match
# ---------------------------------------------------------------------------

@router.post(
    "/runs/{run_id}/match",
    response_model=MatchStageOut,
    status_code=status.HTTP_200_OK,
    summary="Matching Agent — deterministic reconciliation of ingested records",
    tags=["orchestrate"],
)
def run_match(run_id: str, db: Session = Depends(get_db)) -> MatchStageOut:
    _get_run_or_404(run_id, db)
    result = match(db, run_id)
    return MatchStageOut(
        run_id=run_id,
        matched=result.matched,
        auto_accepted=result.auto_accepted,
        needs_review=result.needs_review,
        unmatched_bank=result.unmatched_bank,
        unmatched_gl=result.unmatched_gl,
        match_ids=result.match_ids,
    )


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/investigate
# ---------------------------------------------------------------------------

@router.post(
    "/runs/{run_id}/investigate",
    response_model=InvestigateStageOut,
    status_code=status.HTTP_200_OK,
    summary="Investigation Agent — classify unmatched records as exceptions",
    tags=["orchestrate"],
)
def run_investigate(run_id: str, db: Session = Depends(get_db)) -> InvestigateStageOut:
    _get_run_or_404(run_id, db)
    exceptions = investigate(db, run_id)
    return InvestigateStageOut(
        run_id=run_id,
        exception_count=len(exceptions),
        exceptions=[ExceptionOut.model_validate(e) for e in exceptions],
    )


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/resolve
# ---------------------------------------------------------------------------

@router.post(
    "/runs/{run_id}/resolve",
    response_model=ResolveStageOut,
    status_code=status.HTTP_200_OK,
    summary="Resolution Agent — draft journal entries for open exceptions",
    tags=["orchestrate"],
)
def run_resolve(run_id: str, db: Session = Depends(get_db)) -> ResolveStageOut:
    _get_run_or_404(run_id, db)
    drafts = resolve(db, run_id)
    return ResolveStageOut(
        run_id=run_id,
        draft_count=len(drafts),
        drafts=[JournalDraftOut.model_validate(d) for d in drafts],
    )
