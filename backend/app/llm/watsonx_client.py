"""
watsonx_client.py — single entry point for all watsonx.ai calls.

Exposes:
    generate_json(prompt: str, schema_hint: str) -> dict

If LLM_MODE=mock the SDK is never imported and a canned response is returned
deterministically based on keywords in the prompt.
"""

from __future__ import annotations

import json
import re

from app.config import settings
from app.llm.masking import mask

# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

_MOCK_RESPONSES: dict[str, dict] = {
    "cause": {
        "cause": "bank_fee",
        "explanation": "Heuristic: small negative charge matching FEE pattern. No GL counterpart found.",
        "confidence": 0.75,
    },
    "duplicate": {
        "cause": "duplicate",
        "explanation": "Same amount and payee appear twice within two days on the same side.",
        "confidence": 0.80,
    },
    "timing": {
        "cause": "timing",
        "explanation": "A matching counterpart exists just outside the reconciliation window.",
        "confidence": 0.70,
    },
    "fx": {
        "cause": "fx_variance",
        "explanation": "Counterpart differs by less than 3% with a different currency.",
        "confidence": 0.72,
    },
    "invoice": {
        "cause": "missing_invoice",
        "explanation": "Bank deposit with no GL counterpart and no other classification signal.",
        "confidence": 0.60,
    },
    "same payee": {
        "same_payee": True,
        "reason": "Payee names are equivalent after normalization.",
    },
}

_DEFAULT_MOCK: dict = {
    "cause": "other",
    "explanation": "No specific pattern detected; flagged for manual review.",
    "confidence": 0.50,
}


def _mock_response(prompt: str) -> dict:
    lower = prompt.lower()
    for keyword, response in _MOCK_RESPONSES.items():
        if keyword in lower:
            return response
    return _DEFAULT_MOCK


# ---------------------------------------------------------------------------
# Real SDK helpers (imported lazily so mock mode never touches them)
# ---------------------------------------------------------------------------


def _build_model():  # type: ignore[return]
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.foundation_models.schema import TextGenParameters

    credentials = Credentials(
        api_key=settings.WATSONX_API_KEY,
        url=settings.WATSONX_URL,
    )
    params = TextGenParameters(
        decoding_method="greedy",
        temperature=0,
        max_new_tokens=300,
    )
    return ModelInference(
        model_id=settings.WATSONX_MODEL_ID,
        credentials=credentials,
        project_id=settings.WATSONX_PROJECT_ID,
        params=params,
        validate=False,
    )


_model = None  # lazily initialised in live mode


def _get_model():
    global _model
    if _model is None:
        _model = _build_model()
    return _model


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Return the first JSON object found in *text*."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output: {text!r}")
    return json.loads(match.group())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_json(prompt: str, schema_hint: str = "") -> dict:
    """
    Mask *prompt*, call the LLM, extract and return a JSON dict.

    Parameters
    ----------
    prompt:      The full prompt to send (will be masked first).
    schema_hint: Hint appended to the prompt describing expected JSON shape.
                 Ignored in mock mode.

    Returns a dict parsed from the model's response.
    One retry on failure before raising.
    """
    safe_prompt = mask(prompt)

    if settings.LLM_MODE == "mock":
        return _mock_response(safe_prompt)

    if schema_hint:
        safe_prompt = f"{safe_prompt}\n\nRespond with JSON matching: {schema_hint}"

    model = _get_model()

    last_exc: Exception | None = None
    for attempt in range(2):  # one retry
        try:
            response: dict = model.generate(  # type: ignore[assignment]
                prompt=safe_prompt,
                params={"timeout": 20},
            )
            raw_text: str = response["results"][0]["generated_text"]
            return _extract_json(raw_text)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    raise RuntimeError(f"generate_json failed after 2 attempts: {last_exc}") from last_exc
