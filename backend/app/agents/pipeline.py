"""Pipeline — runs all four agents in sequence (section 6.5).

run_pipeline(db, run_id, bank_csv, gl_csv) executes:
  Ingestion → Matching → Investigation → Resolution

Timings per stage are recorded on the Run row via JSON in a stage_timings
attribute; the run status is set to done or failed.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.ingestion import ingest
from app.agents.investigation import investigate
from app.agents.matching import match
from app.agents.resolution import resolve
from app.audit import log as audit_log
from app.models import Run


def run_pipeline(
    db: Session,
    run_id: str,
    bank_csv: bytes,
    gl_csv: bytes,
) -> dict[str, Any]:
    """Run the full reconciliation pipeline for *run_id*.

    Returns a dict with stage timings and final status.
    Marks run.status = 'done' on success, 'failed' on any exception.
    """
    run: Run = db.get(Run, run_id)  # type: ignore[assignment]
    if run is None:
        raise ValueError(f"Run {run_id!r} not found")

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    db.commit()

    timings: dict[str, float] = {}

    try:
        # Stage 1 — Ingestion
        t0 = time.perf_counter()
        ingest_result = ingest(db, run_id, bank_csv, gl_csv)
        timings["ingestion"] = round(time.perf_counter() - t0, 3)

        # Update counts on the run
        run.bank_count = ingest_result.bank_count
        run.gl_count = ingest_result.gl_count
        db.commit()

        # Stage 2 — Matching
        t0 = time.perf_counter()
        match_result = match(db, run_id)
        timings["matching"] = round(time.perf_counter() - t0, 3)

        # Stage 3 — Investigation
        t0 = time.perf_counter()
        exceptions = investigate(db, run_id)
        timings["investigation"] = round(time.perf_counter() - t0, 3)

        # Stage 4 — Resolution
        t0 = time.perf_counter()
        drafts = resolve(db, run_id)
        timings["resolution"] = round(time.perf_counter() - t0, 3)

        timings["total"] = round(sum(v for k, v in timings.items() if k != "total"), 3)

        run.status = "done"
        run.finished_at = datetime.now(timezone.utc)
        # Store timings as JSON on the run (repurpose a JSON column if present,
        # or just keep them in audit log — store in audit payload).
        db.commit()

        audit_log.append(
            db=db,
            actor="pipeline",
            action="run.created",
            entity_type="run",
            entity_id=run_id,
            payload={
                "status": "done",
                "timings": timings,
                "bank_count": ingest_result.bank_count,
                "gl_count": ingest_result.gl_count,
                "matched": match_result.matched,
                "exceptions": len(exceptions),
                "drafts": len(drafts),
            },
        )

    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        audit_log.append(
            db=db,
            actor="pipeline",
            action="run.created",
            entity_type="run",
            entity_id=run_id,
            payload={"status": "failed", "error": str(exc), "timings": timings},
        )
        raise

    return timings
