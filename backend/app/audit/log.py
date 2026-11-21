import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def _canonical(seq: int, ts: datetime, actor: str, action: str,
                entity_type: str, entity_id: str, payload: dict) -> str:
    """Return deterministic JSON string for hashing."""
    obj = {
        "seq": seq,
        "ts": ts.isoformat(),
        "actor": actor,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "payload": payload,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def append(
    db: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
) -> AuditLog:
    """Append one row to the audit log and return it."""
    last = db.query(AuditLog).order_by(AuditLog.seq.desc()).first()
    prev_hash = last.hash if last else "GENESIS"

    ts = datetime.now(timezone.utc)
    # seq is autoincrement; we need to flush to get it, so insert first
    row = AuditLog(
        ts=ts,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        prev_hash=prev_hash,
        hash="",  # placeholder until we know seq
    )
    db.add(row)
    db.flush()  # assigns row.seq without committing
    db.refresh(row)  # reload ts as stored by the DB (avoids precision drift)

    canonical = _canonical(row.seq, row.ts, actor, action, entity_type, entity_id, payload)
    row.hash = _sha256(prev_hash, canonical)
    db.commit()
    db.refresh(row)
    return row


def verify(db: Session) -> dict[str, Any]:
    """Recompute the hash chain and return {valid, broken_at_seq}."""
    rows = db.query(AuditLog).order_by(AuditLog.seq).all()
    prev_hash = "GENESIS"
    for row in rows:
        canonical = _canonical(
            row.seq, row.ts, row.actor, row.action,
            row.entity_type, row.entity_id, row.payload,
        )
        expected = _sha256(prev_hash, canonical)
        if row.hash != expected or row.prev_hash != prev_hash:
            return {"valid": False, "broken_at_seq": row.seq}
        prev_hash = row.hash
    return {"valid": True, "broken_at_seq": None}
