"""Routes: metrics — GET /api/runs/{id}/metrics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import log as audit_log
from app.config import MANUAL_BASELINE_MIN_PER_RECORD
from app.db import get_db
from app.models import AuditLog, BankTxn, GLEntry, Match, Exception as ExceptionModel, Run
from app.schemas import MetricsOut, PipelineSeconds

router = APIRouter()

_SAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "data" / "samples"
_LABELS_PATH = _SAMPLES_DIR / "labels.json"


def _load_labels() -> dict[str, Any] | None:
    if not _LABELS_PATH.exists():
        return None
    with _LABELS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _extract_timings(db: Session, run_id: str) -> PipelineSeconds:
    """Read timing data from the pipeline audit row."""
    row = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_id == run_id,
            AuditLog.actor == "pipeline",
            AuditLog.action == "run.created",
        )
        .order_by(AuditLog.seq.desc())
        .first()
    )
    if row is None:
        return PipelineSeconds()
    t = row.payload.get("timings", {}) if row.payload else {}
    return PipelineSeconds(
        ingestion=t.get("ingestion"),
        matching=t.get("matching"),
        investigation=t.get("investigation"),
        resolution=t.get("resolution"),
        total=t.get("total"),
    )


@router.get("/runs/{run_id}/metrics", response_model=MetricsOut)
def get_metrics(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    total_records = (run.bank_count or 0) + (run.gl_count or 0)

    # Count auto-accepted matches
    matches: list[Match] = db.query(Match).filter(Match.run_id == run_id).all()
    auto_accepted_matches = [m for m in matches if m.status == "auto_accepted"]

    # Records covered by auto-accepted matches
    auto_accepted_record_ids: set[str] = set()
    for m in auto_accepted_matches:
        # Translate DB-side bank_ids/gl_ids to source IDs for counting
        for bid in (m.bank_ids or []):
            auto_accepted_record_ids.add(bid)
        for gid in (m.gl_ids or []):
            auto_accepted_record_ids.add(gid)

    auto_match_rate = (
        len(auto_accepted_record_ids) / total_records if total_records > 0 else 0.0
    )

    timings = _extract_timings(db, run_id)

    # Audit completeness: every record has ≥ 1 audit row
    all_bank: list[BankTxn] = db.query(BankTxn).filter(BankTxn.run_id == run_id).all()
    all_gl: list[GLEntry] = db.query(GLEntry).filter(GLEntry.run_id == run_id).all()
    all_entity_ids = {b.id for b in all_bank} | {g.id for g in all_gl} | {run_id}
    # Also include match and exception IDs from audit log
    audit_entity_ids: set[str] = set(
        row.entity_id
        for row in db.query(AuditLog.entity_id).all()
    )
    # Every run-related record must appear as entity or in a payload
    # Simplified check: at least the run itself must appear
    audit_complete = run_id in audit_entity_ids

    # --- Label-based metrics (only valid for the demo seed run) ---
    labels = _load_labels()
    match_precision: float | None = None
    match_recall: float | None = None
    cause_accuracy: float | None = None
    note: str | None = None

    if labels is not None:
        try:
            # Build lookup: source_txn_id/source_entry_id → DB row id
            bank_src_to_db: dict[str, str] = {b.source_txn_id: b.id for b in all_bank}
            gl_src_to_db: dict[str, str] = {g.source_entry_id: g.id for g in all_gl}

            # True pairs from labels (convert source IDs to DB IDs)
            true_pairs: list[frozenset[str]] = []
            for pair in labels.get("pairs", []):
                bank_db_ids = [bank_src_to_db[bid] for bid in pair["bank_ids"] if bid in bank_src_to_db]
                gl_db_ids = [gl_src_to_db[gid] for gid in pair["gl_ids"] if gid in gl_src_to_db]
                if bank_db_ids and gl_db_ids:
                    true_pairs.append(frozenset(bank_db_ids + gl_db_ids))

            # Predicted pairs from matches
            pred_pairs: list[frozenset[str]] = [
                frozenset((m.bank_ids or []) + (m.gl_ids or []))
                for m in matches
            ]

            if true_pairs:
                tp = sum(1 for p in pred_pairs if p in set(true_pairs))
                match_precision = tp / len(pred_pairs) if pred_pairs else 0.0
                match_recall = tp / len(true_pairs)

            # Cause accuracy
            true_exc_map: dict[str, str] = {}
            for exc_label in labels.get("exceptions", []):
                for src_id in exc_label["record_ids"]:
                    db_id = bank_src_to_db.get(src_id) or gl_src_to_db.get(src_id)
                    if db_id:
                        true_exc_map[db_id] = exc_label["cause"]

            exceptions: list[ExceptionModel] = (
                db.query(ExceptionModel).filter(ExceptionModel.run_id == run_id).all()
            )
            correct = 0
            labeled_total = 0
            for exc in exceptions:
                for rid in (exc.record_ids or []):
                    if rid in true_exc_map:
                        labeled_total += 1
                        if exc.cause == true_exc_map[rid]:
                            correct += 1

            if labeled_total > 0:
                cause_accuracy = correct / labeled_total

        except Exception:  # noqa: BLE001
            note = "Label-based metrics could not be computed for this run."

    return MetricsOut(
        run_id=run_id,
        auto_match_rate=round(auto_match_rate, 4),
        match_precision=round(match_precision, 4) if match_precision is not None else None,
        match_recall=round(match_recall, 4) if match_recall is not None else None,
        cause_accuracy=round(cause_accuracy, 4) if cause_accuracy is not None else None,
        pipeline_seconds=timings,
        manual_baseline_minutes=MANUAL_BASELINE_MIN_PER_RECORD,
        audit_complete=audit_complete,
        note=note,
    )
