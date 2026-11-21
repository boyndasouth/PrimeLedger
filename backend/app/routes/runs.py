"""Routes: runs — POST /api/runs, GET /api/runs/{id}, matches, exceptions, demo seed."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session

from app.agents.pipeline import run_pipeline
from app.audit import log as audit_log
from app.db import get_db
from app.models import BankTxn, GLEntry, Match, Exception as ExceptionModel, Run
from app.schemas import (
    ExceptionOut,
    MatchOut,
    RunSummary,
    SeedOut,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# POST /api/runs — create run, execute pipeline, return summary
# ---------------------------------------------------------------------------

@router.post("/runs", response_model=RunSummary, status_code=status.HTTP_201_CREATED)
async def create_run(
    bank_file: UploadFile = File(...),
    gl_file: UploadFile = File(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    db: Session = Depends(get_db),
):
    bank_csv = await bank_file.read()
    gl_csv = await gl_file.read()

    run = Run(
        id=str(uuid.uuid4()),
        period_start=period_start,
        period_end=period_end,
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    audit_log.append(
        db=db,
        actor="api",
        action="run.created",
        entity_type="run",
        entity_id=run.id,
        payload={"period_start": period_start, "period_end": period_end},
    )

    try:
        run_pipeline(db, run.id, bank_csv, gl_csv)
    except Exception as exc:  # noqa: BLE001
        db.refresh(run)
        return run

    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# GET /api/runs/{id} — run status + counts
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}", response_model=RunSummary)
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/matches?status=
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/matches", response_model=list[MatchOut])
def get_matches(run_id: str, status: str | None = None, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    q = db.query(Match).filter(Match.run_id == run_id)
    if status:
        q = q.filter(Match.status == status)
    return q.all()


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/exceptions?status=
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/exceptions", response_model=list[ExceptionOut])
def get_exceptions(run_id: str, status: str | None = None, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    q = db.query(ExceptionModel).filter(ExceptionModel.run_id == run_id)
    if status:
        q = q.filter(ExceptionModel.status == status)
    return q.all()


# ---------------------------------------------------------------------------
# POST /api/demo/seed — load committed sample CSVs and run pipeline
# ---------------------------------------------------------------------------

_SAMPLES_DIR = Path(__file__).parent.parent.parent / "data" / "samples"


@router.post("/demo/seed", response_model=SeedOut, status_code=status.HTTP_201_CREATED)
def demo_seed(db: Session = Depends(get_db)):
    bank_csv = (_SAMPLES_DIR / "bank.csv").read_bytes()
    gl_csv = (_SAMPLES_DIR / "gl.csv").read_bytes()

    run = Run(
        id=str(uuid.uuid4()),
        period_start="2024-01-01",
        period_end="2024-01-31",
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    audit_log.append(
        db=db,
        actor="api",
        action="run.created",
        entity_type="run",
        entity_id=run.id,
        payload={"source": "demo_seed"},
    )

    try:
        run_pipeline(db, run.id, bank_csv, gl_csv)
    except Exception:  # noqa: BLE001
        pass

    db.refresh(run)
    return SeedOut(
        run_id=run.id,
        status=run.status,
        bank_count=run.bank_count,
        gl_count=run.gl_count,
    )
