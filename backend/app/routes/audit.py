"""Routes: audit — GET /api/runs/{id}/audit, GET /api/audit/verify."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import log as audit_log
from app.db import get_db
from app.models import AuditLog, Run
from app.schemas import AuditLogOut, VerifyOut

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/audit — audit rows for a run
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/audit", response_model=list[AuditLogOut])
def get_run_audit(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Return all audit rows that reference this run as entity_id OR whose
    # payload contains the run_id — we keep it simple: filter by entity_id=run_id
    # plus the broader set of rows where payload->run_id equals this run.
    rows = (
        db.query(AuditLog)
        .filter(
            (AuditLog.entity_id == run_id)
            | (AuditLog.payload["run_id"].as_string() == run_id)
        )
        .order_by(AuditLog.seq)
        .all()
    )
    return rows


# ---------------------------------------------------------------------------
# GET /api/audit/verify — chain integrity
# ---------------------------------------------------------------------------

@router.get("/audit/verify", response_model=VerifyOut)
def verify_audit(db: Session = Depends(get_db)):
    result = audit_log.verify(db)
    return VerifyOut(**result)
