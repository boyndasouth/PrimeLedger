"""
generate_synthetic.py — PrimeLedger build step 1.

Usage:
    python generate_synthetic.py [--seed 42] [--rows 300] [--out backend/data/samples]

Writes:
    <out>/bank.csv    — txn_id, date, description, amount, currency, balance
    <out>/gl.csv      — entry_id, date, account, payee, memo, amount, currency, reference
    <out>/labels.json — {"pairs": [...], "exceptions": [...]}
"""
from __future__ import annotations

import argparse
import json
import os
import random
import string
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

VENDORS = [
    ("Amazon Web Services", "AMZN MKTP US*2K4", "6050", "AWS"),
    ("Salesforce Inc", "SALESFORCE.COM", "6060", "SFD"),
    ("Slack Technologies", "SLACK *MONTHLY", "6070", "SLK"),
    ("Google Workspace", "GOOGLE *GSUITE", "6080", "GGL"),
    ("Microsoft Azure", "MICROSOFT*AZURE", "6090", "MSF"),
    ("Zoom Video Comms", "ZOOM.US 888-799", "6100", "ZOM"),
    ("Dropbox Inc", "DROPBOX*PLUS", "6110", "DBX"),
    ("Stripe Inc", "STRIPE PAYMENTS", "6120", "STR"),
    ("Twilio Inc", "TWILIO*CLOUD", "6130", "TWL"),
    ("HubSpot Inc", "HUBSPOT*CRM", "6140", "HBS"),
    ("Atlassian Corp", "ATLASSIAN*JIRA", "6150", "ATL"),
    ("DocuSign Inc", "DOCUSIGN*ESIGN", "6160", "DCU"),
    ("Okta Inc", "OKTA IDENTITY", "6170", "OKT"),
    ("Datadog Inc", "DATADOG*MONITOR", "6180", "DDG"),
    ("Adobe Systems", "ADOBE ACROPRO", "6190", "ADB"),
    ("Shopify Inc", "SHOPIFY*STORE", "6200", "SHF"),
    ("Canva Pty Ltd", "CANVA* DESIGN", "6210", "CNV"),
    ("Cloudflare Inc", "CLOUDFLARE *NET", "6220", "CLF"),
    ("GitHub Inc", "GITHUB.COM", "6230", "GHB"),
    ("Figma Inc", "FIGMA MONTHLY", "6240", "FGM"),
]

FUZZY_VARIANTS: dict[str, str] = {
    "Amazon Web Services": "AMZN MKTP US*2K4",
    "Salesforce Inc": "SFDC SALESFORCE",
    "Slack Technologies": "SLACK TECHNOLOG",
    "Google Workspace": "GOOGLE *CLOUD",
    "Microsoft Azure": "MSFT AZURE SVCS",
    "Zoom Video Comms": "ZOOM CONFERENC",
    "Dropbox Inc": "DROPBOX STORAGE",
    "Stripe Inc": "STRIPE INC USA",
    "Twilio Inc": "TWILIO COMMUN",
    "HubSpot Inc": "HUBSPOT MARKET",
    "Atlassian Corp": "ATLASSIAN*CONF",
    "DocuSign Inc": "DOCUSIGN INC",
    "Okta Inc": "OKTA INC AUTH",
    "Datadog Inc": "DATADOG INC",
    "Adobe Systems": "ADOBE CREATIVE",
}

ACCOUNTS = ["6050", "6060", "6070", "6080", "6090", "6100", "6110",
            "6120", "6130", "6140", "6150", "6160", "6170", "6180",
            "6190", "6200", "6210", "6220", "6230", "6240"]

CURRENCIES = ["USD"] * 14 + ["EUR"] * 3 + ["GBP"] * 3  # ~70% USD

PERIOD_START = date(2025, 1, 1)
PERIOD_END = date(2025, 1, 31)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand_date(rng: random.Random, start: date = PERIOD_START, end: date = PERIOD_END) -> date:
    delta = (end - start).days
    return start + timedelta(days=rng.randint(0, delta))


def _rand_amount(rng: random.Random, lo: float = 50, hi: float = 5000) -> Decimal:
    raw = rng.uniform(lo, hi)
    return Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _rand_id(rng: random.Random, prefix: str, n: int) -> str:
    suffix = "".join(rng.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}{n:04d}{suffix}"


def _rand_ref(rng: random.Random) -> str:
    return "REF-" + "".join(rng.choices(string.digits, k=8))


def _pick_vendor(rng: random.Random):
    return rng.choice(VENDORS)


def _pick_currency(rng: random.Random) -> str:
    return rng.choice(CURRENCIES)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(seed: int, rows: int, out_dir: str) -> None:
    rng = random.Random(seed)

    # target counts (sum = rows)
    n_exact = int(rows * 0.70)
    n_fuzzy = int(rows * 0.10)
    n_split = int(rows * 0.05)   # each split = 2–3 bank rows, 1 GL row (or vice versa)
    n_exc = rows - n_exact - n_fuzzy - n_split  # ~15%

    bank_rows: list[dict] = []
    gl_rows: list[dict] = []
    pairs: list[dict] = []
    exceptions: list[dict] = []

    b_seq = 0
    g_seq = 0

    # -- exact matches -------------------------------------------------------
    for _ in range(n_exact):
        b_seq += 1
        g_seq += 1
        vendor, bank_desc, account, _short = _pick_vendor(rng)
        currency = _pick_currency(rng)
        amount = -_rand_amount(rng)              # withdrawals (negative)
        d = _rand_date(rng)
        gl_date = d + timedelta(days=rng.randint(0, 1))  # ±1 day

        bid = _rand_id(rng, "B", b_seq)
        gid = _rand_id(rng, "G", g_seq)

        bank_rows.append({
            "txn_id": bid, "date": d.isoformat(),
            "description": bank_desc, "amount": str(amount),
            "currency": currency, "balance": "",
        })
        gl_rows.append({
            "entry_id": gid, "date": gl_date.isoformat(),
            "account": account, "payee": vendor, "memo": f"Inv {_rand_ref(rng)}",
            "amount": str(amount), "currency": currency, "reference": _rand_ref(rng),
        })
        pairs.append({"bank_ids": [bid], "gl_ids": [gid], "type": "exact"})

    # -- fuzzy payee variants ------------------------------------------------
    for _ in range(n_fuzzy):
        b_seq += 1
        g_seq += 1
        vendor, bank_desc, account, _short = _pick_vendor(rng)
        currency = _pick_currency(rng)
        amount = -_rand_amount(rng)
        d = _rand_date(rng)
        gl_date = d + timedelta(days=rng.randint(0, 5))  # ±5 days

        bid = _rand_id(rng, "B", b_seq)
        gid = _rand_id(rng, "G", g_seq)

        bank_desc_fuzzy = FUZZY_VARIANTS.get(vendor, bank_desc)

        bank_rows.append({
            "txn_id": bid, "date": d.isoformat(),
            "description": bank_desc_fuzzy, "amount": str(amount),
            "currency": currency, "balance": "",
        })
        gl_rows.append({
            "entry_id": gid, "date": gl_date.isoformat(),
            "account": account, "payee": vendor, "memo": f"Inv {_rand_ref(rng)}",
            "amount": str(amount), "currency": currency, "reference": _rand_ref(rng),
        })
        pairs.append({"bank_ids": [bid], "gl_ids": [gid], "type": "fuzzy"})

    # -- split/combined payments ---------------------------------------------
    for _ in range(n_split):
        g_seq += 1
        vendor, bank_desc, account, _short = _pick_vendor(rng)
        currency = _pick_currency(rng)
        total = _rand_amount(rng, 200, 3000)
        d = _rand_date(rng)
        gl_date = d + timedelta(days=rng.randint(0, 2))

        gid = _rand_id(rng, "G", g_seq)
        gl_rows.append({
            "entry_id": gid, "date": gl_date.isoformat(),
            "account": account, "payee": vendor, "memo": f"Inv {_rand_ref(rng)}",
            "amount": str(-total), "currency": currency, "reference": _rand_ref(rng),
        })

        # 2 or 3 bank rows that sum to total
        n_parts = rng.randint(2, 3)
        part_amounts = []
        remaining = total
        for i in range(n_parts - 1):
            part = (remaining * Decimal(str(rng.uniform(0.3, 0.6)))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            part_amounts.append(part)
            remaining -= part
        part_amounts.append(remaining)

        bids = []
        for part in part_amounts:
            b_seq += 1
            bid = _rand_id(rng, "B", b_seq)
            bids.append(bid)
            bank_rows.append({
                "txn_id": bid, "date": (_rand_date(rng, d, d + timedelta(days=2))).isoformat(),
                "description": bank_desc, "amount": str(-part),
                "currency": currency, "balance": "",
            })

        pairs.append({"bank_ids": bids, "gl_ids": [gid], "type": "one_to_many"})

    # -- exceptions ----------------------------------------------------------
    exc_causes = ["bank_fee", "duplicate", "timing", "fx_variance", "missing_invoice"]
    per_cause = max(1, n_exc // len(exc_causes))

    for cause in exc_causes:
        count = per_cause
        if cause == exc_causes[-1]:
            count = n_exc - per_cause * (len(exc_causes) - 1)

        for _ in range(count):
            side = rng.choice(["bank", "gl"])
            vendor, bank_desc, account, _short = _pick_vendor(rng)
            currency = _pick_currency(rng)
            amount = _rand_amount(rng, 10 if cause == "bank_fee" else 50, 200 if cause == "bank_fee" else 5000)
            d = _rand_date(rng)

            if side == "bank":
                b_seq += 1
                rid = _rand_id(rng, "B", b_seq)
                desc = bank_desc if cause != "bank_fee" else "MONTHLY SERVICE FEE"
                bank_rows.append({
                    "txn_id": rid, "date": d.isoformat(),
                    "description": desc, "amount": str(-amount),
                    "currency": currency, "balance": "",
                })
            else:
                g_seq += 1
                rid = _rand_id(rng, "G", g_seq)
                gl_rows.append({
                    "entry_id": rid, "date": d.isoformat(),
                    "account": account, "payee": vendor, "memo": f"Inv {_rand_ref(rng)}",
                    "amount": str(-amount), "currency": currency, "reference": _rand_ref(rng),
                })

            exceptions.append({"side": side, "record_ids": [rid], "cause": cause})

    # -- build DataFrames and write CSV --------------------------------------
    os.makedirs(out_dir, exist_ok=True)

    bank_df = pd.DataFrame(bank_rows, columns=["txn_id", "date", "description", "amount", "currency", "balance"])
    gl_df = pd.DataFrame(gl_rows, columns=["entry_id", "date", "account", "payee", "memo", "amount", "currency", "reference"])

    bank_df.to_csv(os.path.join(out_dir, "bank.csv"), index=False)
    gl_df.to_csv(os.path.join(out_dir, "gl.csv"), index=False)

    labels = {"pairs": pairs, "exceptions": exceptions}
    with open(os.path.join(out_dir, "labels.json"), "w", encoding="utf-8") as fh:
        json.dump(labels, fh, indent=2)

    print(
        f"Wrote {len(bank_rows)} bank rows, {len(gl_rows)} GL rows, "
        f"{len(pairs)} pairs, {len(exceptions)} exceptions -> {out_dir}"
    )


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic reconciliation data.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rows", type=int, default=300)
    parser.add_argument("--out", default=os.path.join("backend", "data", "samples"))
    args = parser.parse_args()
    generate(seed=args.seed, rows=args.rows, out_dir=args.out)


if __name__ == "__main__":
    main()
