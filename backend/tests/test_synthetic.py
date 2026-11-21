"""
test_synthetic.py — Checks for the synthetic data generator.

1. Same seed produces identical bank.csv, gl.csv, labels.json.
2. Every record appears exactly once across pairs + exceptions in labels.json.
"""
import json
import os
import tempfile

import pandas as pd
import pytest

# Allow running from repo root or backend/ directory
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))
from generate_synthetic import generate


SEED = 42
ROWS = 300


def _run(seed: int = SEED, rows: int = ROWS) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        generate(seed=seed, rows=rows, out_dir=tmp)
        bank = pd.read_csv(os.path.join(tmp, "bank.csv"), dtype=str)
        gl = pd.read_csv(os.path.join(tmp, "gl.csv"), dtype=str)
        with open(os.path.join(tmp, "labels.json"), encoding="utf-8") as fh:
            labels = json.load(fh)
    return bank, gl, labels


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_same_bank(self):
        b1, _, _ = _run()
        b2, _, _ = _run()
        pd.testing.assert_frame_equal(b1, b2)

    def test_same_seed_same_gl(self):
        _, g1, _ = _run()
        _, g2, _ = _run()
        pd.testing.assert_frame_equal(g1, g2)

    def test_same_seed_same_labels(self):
        _, _, l1 = _run()
        _, _, l2 = _run()
        assert l1 == l2

    def test_different_seed_different_output(self):
        b1, _, _ = _run(seed=42)
        b2, _, _ = _run(seed=99)
        assert not b1["txn_id"].equals(b2["txn_id"])


# ---------------------------------------------------------------------------
# Coverage: every record appears exactly once in labels.json
# ---------------------------------------------------------------------------

class TestLabelCoverage:
    def _collect_bank_ids(self, labels: dict) -> list[str]:
        ids: list[str] = []
        for p in labels["pairs"]:
            ids.extend(p["bank_ids"])
        for e in labels["exceptions"]:
            if e["side"] == "bank":
                ids.extend(e["record_ids"])
        return ids

    def _collect_gl_ids(self, labels: dict) -> list[str]:
        ids: list[str] = []
        for p in labels["pairs"]:
            ids.extend(p["gl_ids"])
        for e in labels["exceptions"]:
            if e["side"] == "gl":
                ids.extend(e["record_ids"])
        return ids

    def test_every_bank_record_labeled_once(self):
        bank, _, labels = _run()
        all_bank_ids = self._collect_bank_ids(labels)
        assert sorted(all_bank_ids) == sorted(bank["txn_id"].tolist()), \
            "Bank IDs in labels do not match bank.csv"
        assert len(all_bank_ids) == len(set(all_bank_ids)), \
            "Duplicate bank IDs found in labels"

    def test_every_gl_record_labeled_once(self):
        _, gl, labels = _run()
        all_gl_ids = self._collect_gl_ids(labels)
        assert sorted(all_gl_ids) == sorted(gl["entry_id"].tolist()), \
            "GL IDs in labels do not match gl.csv"
        assert len(all_gl_ids) == len(set(all_gl_ids)), \
            "Duplicate GL IDs found in labels"
