"""Ingestion Agent — parse CSVs, normalise, insert into DB, audit."""
import io
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy.orm import Session

from app.audit import log as audit_log
from app.config import FX_RATES
from app.models import BankTxn, GLEntry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BANK_REQUIRED = {"txn_id", "date", "description", "amount", "currency"}
GL_REQUIRED = {"entry_id", "date", "account", "payee", "memo", "amount", "currency"}

_NOISE = re.compile(
    r"\b(POS|ACH|DEBIT|CARD|PURCHASE|PYMT|PMT)\b",
    re.IGNORECASE,
)
# Strip trailing tokens that contain at least one digit (e.g. "2K4", "AB1234", "REF123").
# Pure-alpha trailing tokens (e.g. "US", "MONTHLY") are kept.
_TRAILING_ID = re.compile(r"\s+[A-Z0-9]*\d[A-Z0-9]*$")
_STAR = re.compile(r"\*+")
_MULTI_SPACE = re.compile(r"\s{2,}")

_ALIASES: dict[str, str] = {
    "AMZN": "AMAZON",
    "AMZN MKTP": "AMAZON",
    "WMT": "WALMART",
    "WHOLEFDS": "WHOLE FOODS",
    "SQ": "SQUARE",
    "PYPL": "PAYPAL",
    "GOOGL": "GOOGLE",
    "GOOG": "GOOGLE",
    "MSFT": "MICROSOFT",
}


# ---------------------------------------------------------------------------
# Public pure function
# ---------------------------------------------------------------------------

def normalize_payee(text: str) -> str:
    """Return a normalised payee name: uppercase, strip noise, apply aliases."""
    if not text:
        return ""
    t = text.upper().strip()
    # Replace * with space
    t = _STAR.sub(" ", t)
    # Remove noise words
    t = _NOISE.sub(" ", t)
    # Strip trailing IDs (run twice to catch chained ones)
    t = _TRAILING_ID.sub("", t).strip()
    t = _TRAILING_ID.sub("", t).strip()
    # Collapse whitespace
    t = _MULTI_SPACE.sub(" ", t).strip()
    # Apply alias dict (longest-match first via sorted keys)
    for alias in sorted(_ALIASES, key=len, reverse=True):
        if t.startswith(alias):
            rest = t[len(alias):].strip()
            t = (_ALIASES[alias] + (" " + rest if rest else "")).strip()
            break
    return t


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class IngestResult:
    bank_count: int = 0
    gl_count: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_decimal(val) -> Decimal:
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"Cannot convert amount to Decimal: {val!r}") from exc


def _to_usd(amount: Decimal, currency: str, warnings: list[str]) -> Decimal:
    rate = FX_RATES.get(currency.upper())
    if rate is None:
        warnings.append(f"Unknown currency {currency!r}; treating rate as 1.00")
        rate = Decimal("1.00")
    return (amount * rate).quantize(Decimal("0.01"))


def _iso_date(val) -> str:
    """Return ISO date string (YYYY-MM-DD) from a pandas-parsed value."""
    return pd.Timestamp(val).strftime("%Y-%m-%d")


def _check_columns(df: pd.DataFrame, required: set[str], label: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"{label} CSV is missing required columns: {', '.join(sorted(missing))}"
        )


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def ingest(db: Session, run_id: str, bank_csv: bytes, gl_csv: bytes) -> IngestResult:
    result = IngestResult()

    # --- Bank ---
    bank_df = pd.read_csv(io.BytesIO(bank_csv))
    _check_columns(bank_df, BANK_REQUIRED, "Bank")

    bank_rows: list[BankTxn] = []
    for _, row in bank_df.iterrows():
        currency = str(row["currency"]).strip().upper()
        amount = _to_decimal(row["amount"])
        txn = BankTxn(
            id=str(uuid.uuid4()),
            run_id=run_id,
            source_txn_id=str(row["txn_id"]),
            date=_iso_date(row["date"]),
            description_raw=str(row["description"]),
            payee_normalized=normalize_payee(str(row["description"])),
            amount=amount,
            currency=currency,
            amount_usd=_to_usd(amount, currency, result.warnings),
        )
        bank_rows.append(txn)

    db.add_all(bank_rows)
    result.bank_count = len(bank_rows)

    # --- GL ---
    gl_df = pd.read_csv(io.BytesIO(gl_csv))
    _check_columns(gl_df, GL_REQUIRED, "GL")

    gl_rows: list[GLEntry] = []
    for _, row in gl_df.iterrows():
        currency = str(row["currency"]).strip().upper()
        amount = _to_decimal(row["amount"])
        payee_raw = str(row["payee"]) if pd.notna(row["payee"]) else None
        entry = GLEntry(
            id=str(uuid.uuid4()),
            run_id=run_id,
            source_entry_id=str(row["entry_id"]),
            date=_iso_date(row["date"]),
            account=str(row["account"]),
            payee_raw=payee_raw,
            payee_normalized=normalize_payee(payee_raw or ""),
            memo=str(row["memo"]) if pd.notna(row["memo"]) else None,
            amount=amount,
            currency=currency,
            amount_usd=_to_usd(amount, currency, result.warnings),
            reference=str(row["reference"]) if "reference" in row and pd.notna(row["reference"]) else None,
        )
        gl_rows.append(entry)

    db.add_all(gl_rows)
    result.gl_count = len(gl_rows)

    db.flush()

    audit_log.append(
        db=db,
        actor="ingestion_agent",
        action="ingest.completed",
        entity_type="run",
        entity_id=run_id,
        payload={"bank_count": result.bank_count, "gl_count": result.gl_count, "warnings": result.warnings},
    )

    return result


# ---------------------------------------------------------------------------
# Stubs for future parsers
# ---------------------------------------------------------------------------

def parse_bai2(data: bytes) -> pd.DataFrame:  # pragma: no cover
    raise NotImplementedError("BAI2 parsing is not yet implemented")


def parse_pdf(data: bytes) -> pd.DataFrame:  # pragma: no cover
    raise NotImplementedError("PDF parsing is not yet implemented")
