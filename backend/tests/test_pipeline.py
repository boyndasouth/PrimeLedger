"""test_pipeline.py — full demo seed run with LLM_MODE=mock.

Asserts:
  - run.status == "done"
  - metrics endpoint returns a valid response
  - pipeline_seconds.total is present
"""
import os
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Force mock mode before any app import
os.environ.setdefault("LLM_MODE", "mock")

from app.db import Base
from app.models import Run, seed_users
from app.agents.pipeline import run_pipeline
from app.routes.metrics import get_metrics


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


_SAMPLES = pathlib.Path(__file__).parent.parent / "data" / "samples"


def _read_samples():
    bank_csv = (_SAMPLES / "bank.csv").read_bytes()
    gl_csv = (_SAMPLES / "gl.csv").read_bytes()
    return bank_csv, gl_csv


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_pipeline_status_done(db):
    bank_csv, gl_csv = _read_samples()

    run = Run(
        id=str(uuid.uuid4()),
        period_start="2025-01-01",
        period_end="2025-01-31",
        status="pending",
    )
    db.add(run)
    db.commit()

    timings = run_pipeline(db, run.id, bank_csv, gl_csv)

    db.refresh(run)
    assert run.status == "done", f"Expected 'done', got {run.status!r}"
    assert run.bank_count > 0
    assert run.gl_count > 0
    assert timings["total"] > 0


def test_pipeline_produces_exceptions(db):
    bank_csv, gl_csv = _read_samples()

    run = Run(
        id=str(uuid.uuid4()),
        period_start="2025-01-01",
        period_end="2025-01-31",
        status="pending",
    )
    db.add(run)
    db.commit()

    run_pipeline(db, run.id, bank_csv, gl_csv)

    from app.models import Exception as ExceptionModel
    exceptions = db.query(ExceptionModel).filter(ExceptionModel.run_id == run.id).all()
    assert len(exceptions) > 0, "Expected at least one exception from sample data"


def test_pipeline_metrics_present(db):
    bank_csv, gl_csv = _read_samples()

    run = Run(
        id=str(uuid.uuid4()),
        period_start="2025-01-01",
        period_end="2025-01-31",
        status="pending",
    )
    db.add(run)
    db.commit()

    run_pipeline(db, run.id, bank_csv, gl_csv)

    metrics = get_metrics(run_id=run.id, db=db)
    assert metrics.run_id == run.id
    assert 0.0 <= metrics.auto_match_rate <= 1.0
    # Pipeline seconds total should be set
    assert metrics.pipeline_seconds.total is not None
    assert metrics.pipeline_seconds.total > 0
    assert metrics.audit_complete is True
