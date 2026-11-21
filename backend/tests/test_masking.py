"""
test_masking.py — verify masking.py and that mock mode returns valid JSON.
"""

import os
import json
import pytest

from app.llm.masking import mask


# ---------------------------------------------------------------------------
# masking.mask() tests
# ---------------------------------------------------------------------------


class TestLongDigitRuns:
    """Digit runs of 6+ are masked; short numbers and amounts survive."""

    def test_6_digit_run_masked(self):
        result = mask("account 123456")
        assert "123456" not in result
        assert "****" in result

    def test_last_4_preserved(self):
        result = mask("ref 12345678")
        assert result.endswith("5678")
        assert "****" in result

    def test_exact_6_digits_masked(self):
        result = mask("id:987654 done")
        assert "987654" not in result
        assert "****7654" in result

    def test_5_digit_number_survives(self):
        result = mask("zip 90210")
        assert "90210" in result

    def test_amount_survives(self):
        result = mask("amount $1,234.56 paid")
        # commas break digit runs, so 1234 (4 digits) survives
        assert "1,234" in result or "1234" in result

    def test_date_survives(self):
        result = mask("date 2024-01-15")
        # longest digit run here is 4 (2024)
        assert "2024" in result

    def test_long_account_number_masked(self):
        result = mask("acct 1234567890 transfer")
        assert "1234567890" not in result
        # last 4 preserved
        assert "7890" in result

    def test_multiple_runs_in_one_string(self):
        result = mask("from 123456789 to 987654321")
        assert "123456789" not in result
        assert "987654321" not in result


class TestIBANRemoval:
    """IBAN patterns are removed entirely."""

    def test_gb_iban_removed(self):
        result = mask("IBAN GB29NWBK60161331926819 for payment")
        assert "GB29NWBK60161331926819" not in result

    def test_de_iban_removed(self):
        result = mask("send to DE89370400440532013000")
        assert "DE89370400440532013000" not in result

    def test_fr_iban_removed(self):
        result = mask("FR7630006000011234567890189 account")
        assert "FR7630006000011234567890189" not in result

    def test_normal_text_survives_iban_pass(self):
        result = mask("Invoice #INV-2024-001 amount $500.00")
        assert "INV-2024-001" in result
        assert "500.00" in result


class TestRoutingNumberRemoval:
    """US ABA routing numbers (9-digit, first digit 0–3) are removed."""

    def test_routing_number_removed(self):
        result = mask("routing 021000021 for wire")
        assert "021000021" not in result

    def test_routing_number_first_digit_0(self):
        result = mask("ABA: 011401533")
        assert "011401533" not in result

    def test_routing_number_first_digit_3(self):
        result = mask("RTN 322271724")
        assert "322271724" not in result

    def test_9_digit_starting_with_4_not_removed_by_routing_re(self):
        # Starts with 4 — does NOT match routing pattern; still gets digit-run masking
        result = mask("num 412345678")
        # routing pattern won't fire, but long-digit rule will mask it
        assert "412345678" not in result  # digit-run masking kicks in

    def test_amount_not_confused_with_routing(self):
        result = mask("paid $1,250.00 on 2024-03-05")
        assert "1,250" in result or "1250" in result


# ---------------------------------------------------------------------------
# mock mode: generate_json returns valid JSON without credentials
# ---------------------------------------------------------------------------


def test_mock_mode_returns_dict(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    # Force settings reload for this test
    import importlib
    import app.config as cfg_module
    import app.llm.watsonx_client as client_module

    cfg_module.settings.LLM_MODE = "mock"

    result = client_module.generate_json(
        prompt="Classify this unmatched bank_fee transaction",
        schema_hint='{"cause": str, "explanation": str, "confidence": float}',
    )
    assert isinstance(result, dict)
    assert "cause" in result or "explanation" in result or "confidence" in result


def test_mock_mode_no_sdk_import(monkeypatch):
    """Mock mode must never import ibm_watsonx_ai."""
    import sys
    import app.config as cfg_module
    import app.llm.watsonx_client as client_module

    cfg_module.settings.LLM_MODE = "mock"

    # Remove ibm_watsonx_ai from sys.modules to verify it won't be imported
    saved = {k: v for k, v in sys.modules.items() if "ibm_watsonx_ai" in k}
    for k in saved:
        sys.modules.pop(k)

    try:
        result = client_module.generate_json(
            prompt="timing mismatch detected",
            schema_hint="",
        )
        assert isinstance(result, dict)
        # SDK should not have been re-imported
        newly_imported = [k for k in sys.modules if "ibm_watsonx_ai" in k]
        assert newly_imported == [], f"SDK was imported in mock mode: {newly_imported}"
    finally:
        sys.modules.update(saved)


def test_mock_mode_deterministic_keyword_routing(monkeypatch):
    """Same keyword always returns the same canned response."""
    import app.config as cfg_module
    import app.llm.watsonx_client as client_module

    cfg_module.settings.LLM_MODE = "mock"

    r1 = client_module.generate_json("duplicate transaction found", "")
    r2 = client_module.generate_json("duplicate payment detected", "")
    assert r1 == r2
    assert r1.get("cause") == "duplicate"


def test_mock_response_is_json_serialisable(monkeypatch):
    import app.config as cfg_module
    import app.llm.watsonx_client as client_module

    cfg_module.settings.LLM_MODE = "mock"

    result = client_module.generate_json("fx_variance amount differs", "")
    # Must round-trip through JSON without error
    serialised = json.dumps(result)
    assert json.loads(serialised) == result
