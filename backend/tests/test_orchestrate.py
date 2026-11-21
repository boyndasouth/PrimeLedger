"""test_orchestrate.py — per-stage Orchestrate routes.

Tests each of:
  POST /api/runs/{id}/ingest
  POST /api/runs/{id}/match
  POST /api/runs/{id}/investigate
  POST /api/runs/{id}/resolve

Each test drives only the stage under test (not the full pipeline) so they
remain fast and isolated.  Uses LLM_MODE=mock.
"""
from __future__ import annotations

import io
import os
import pathlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("LLM_MODE", "mock")

from app.db import Base, get_db
from app.main import app
from app.models import Run, seed_users

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLES = pathlib.Path(__file__).parent.parent / "data" / "samples"


@pytest.fixture()
def db_session():
    # StaticPool forces a single shared connection so SQLite :memory: tables
    # created by create_all() are visible to every session opened from this engine.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    seed_users(session)
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db_session):
    """TestClient with the in-memory DB injected."""
    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def run_id(db_session) -> str:
    run = Run(
        id=str(uuid.uuid4()),
        period_start="2024-01-01",
        period_end="2024-01-31",
        status="pending",
    )
    db_session.add(run)
    db_session.commit()
    return run.id


def _csv_files():
    bank_csv = (_SAMPLES / "bank.csv").read_bytes()
    gl_csv = (_SAMPLES / "gl.csv").read_bytes()
    return bank_csv, gl_csv


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/ingest
# ---------------------------------------------------------------------------

def test_ingest_stage_returns_counts(client, run_id):
    bank_csv, gl_csv = _csv_files()
    resp = client.post(
        f"/api/runs/{run_id}/ingest",
        files={
            "bank_file": ("bank.csv", io.BytesIO(bank_csv), "text/csv"),
            "gl_file": ("gl.csv", io.BytesIO(gl_csv), "text/csv"),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["bank_count"] > 0
    assert body["gl_count"] > 0
    assert isinstance(body["warnings"], list)


def test_ingest_stage_404_on_missing_run(client):
    bank_csv, gl_csv = _csv_files()
    resp = client.post(
        "/api/runs/nonexistent-run-id/ingest",
        files={
            "bank_file": ("bank.csv", io.BytesIO(bank_csv), "text/csv"),
            "gl_file": ("gl.csv", io.BytesIO(gl_csv), "text/csv"),
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/match
# ---------------------------------------------------------------------------

def test_match_stage_returns_summary(client, run_id):
    # Ingest first so there are records to match
    bank_csv, gl_csv = _csv_files()
    client.post(
        f"/api/runs/{run_id}/ingest",
        files={
            "bank_file": ("bank.csv", io.BytesIO(bank_csv), "text/csv"),
            "gl_file": ("gl.csv", io.BytesIO(gl_csv), "text/csv"),
        },
    )
    resp = client.post(f"/api/runs/{run_id}/match")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert isinstance(body["matched"], int)
    assert isinstance(body["auto_accepted"], int)
    assert isinstance(body["match_ids"], list)


def test_match_stage_404_on_missing_run(client):
    resp = client.post("/api/runs/does-not-exist/match")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/investigate
# ---------------------------------------------------------------------------

def _ingest_and_match(client, run_id):
    bank_csv, gl_csv = _csv_files()
    client.post(
        f"/api/runs/{run_id}/ingest",
        files={
            "bank_file": ("bank.csv", io.BytesIO(bank_csv), "text/csv"),
            "gl_file": ("gl.csv", io.BytesIO(gl_csv), "text/csv"),
        },
    )
    client.post(f"/api/runs/{run_id}/match")


def test_investigate_stage_returns_exceptions(client, run_id):
    _ingest_and_match(client, run_id)
    resp = client.post(f"/api/runs/{run_id}/investigate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["exception_count"] == len(body["exceptions"])
    assert body["exception_count"] > 0


def test_investigate_stage_404_on_missing_run(client):
    resp = client.post("/api/runs/does-not-exist/investigate")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/resolve
# ---------------------------------------------------------------------------

def _ingest_match_investigate(client, run_id):
    _ingest_and_match(client, run_id)
    client.post(f"/api/runs/{run_id}/investigate")


def test_resolve_stage_returns_drafts(client, run_id):
    _ingest_match_investigate(client, run_id)
    resp = client.post(f"/api/runs/{run_id}/resolve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["draft_count"] == len(body["drafts"])
    assert body["draft_count"] > 0


def test_resolve_stage_drafts_are_balanced(client, run_id):
    """Every non-timing draft must have equal total debits and credits."""
    from decimal import Decimal

    _ingest_match_investigate(client, run_id)
    resp = client.post(f"/api/runs/{run_id}/resolve")
    assert resp.status_code == 200, resp.text
    for draft in resp.json()["drafts"]:
        lines = draft["lines"]
        if not lines:
            continue  # timing exception — no lines
        total_debit = sum(Decimal(str(ln["debit"])) for ln in lines)
        total_credit = sum(Decimal(str(ln["credit"])) for ln in lines)
        assert total_debit == total_credit, (
            f"Draft {draft['id']} is unbalanced: debit={total_debit} credit={total_credit}"
        )


def test_resolve_stage_404_on_missing_run(client):
    resp = client.post("/api/runs/does-not-exist/resolve")
    assert resp.status_code == 404
