"""
masking.py — scrub sensitive account identifiers from text before it reaches any LLM prompt.

Rules (section 7):
  1. Digit runs of 6 or more digits → keep last 4, replace preceding digits with ****.
  2. IBAN patterns (e.g. GB29NWBK60161331926819) → removed entirely.
  3. US ABA routing numbers (9-digit sequences starting with 0–3) → removed entirely.
"""

import re

# IBAN: 2-letter country code, 2 check digits, up to 30 alphanumeric chars; at least 15 total chars.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")

# US ABA routing number: exactly 9 digits, first digit 0–3.
_ROUTING_RE = re.compile(r"\b[0-3]\d{8}\b")

# Any run of 6 or more consecutive digits (applied after IBAN/routing removal).
_LONG_DIGIT_RE = re.compile(r"\d{6,}")


def _mask_long_digits(m: re.Match) -> str:  # type: ignore[type-arg]
    s = m.group()
    if len(s) <= 4:
        return s
    return "****" + s[-4:]


def mask(text: str) -> str:
    """Return *text* with account numbers, IBANs, and routing numbers redacted."""
    text = _IBAN_RE.sub("", text)
    text = _ROUTING_RE.sub("", text)
    text = _LONG_DIGIT_RE.sub(_mask_long_digits, text)
    return text
