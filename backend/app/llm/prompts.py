"""
prompts.py — prompt templates for the LLM agents.

PROMPTS keys are used by the investigation and resolution agents.
Values are format strings; call .format(**kwargs) before passing to generate_json().
"""

PROMPTS: dict[str, str] = {
    "memo": (
        "You are a financial reconciliation assistant. Write a concise journal entry memo.\n"
        "\n"
        "EXCEPTION CAUSE : {cause}\n"
        "EXPLANATION     : {explanation}\n"
        "AMOUNT          : {amount} {currency}\n"
        "DEBIT ACCOUNT   : {debit_account}\n"
        "CREDIT ACCOUNT  : {credit_account}\n"
        "\n"
        "Write ONE short memo sentence (≤15 words) for this journal entry. "
        "Return ONLY a JSON object with a single key \"memo\".\n"
        "Example: {{\"memo\": \"Bank service charge for January 2025.\"}}"
    ),
    "investigate": (
        "You are a financial reconciliation assistant. Classify the unmatched record below.\n"
        "\n"
        "UNMATCHED RECORD\n"
        "  ID          : {record_id}\n"
        "  Date        : {record_date}\n"
        "  Amount      : {record_amount} {record_currency}\n"
        "  Payee       : {record_payee}\n"
        "  Description : {record_description}\n"
        "\n"
        "NEAREST CANDIDATES ON THE OTHER SIDE\n"
        "{candidates}\n"
        "\n"
        "HEURISTIC HINTS\n"
        "{hints}\n"
        "\n"
        "TASK\n"
        "Return ONLY a JSON object — no markdown, no prose — with these exact keys:\n"
        '  "cause"       : one of timing | bank_fee | duplicate | fx_variance | missing_invoice | other\n'
        '  "explanation" : at most 2 sentences in plain English citing the record IDs used\n'
        '  "confidence"  : a float between 0.0 and 1.0\n'
        "\n"
        "Example: "
        '{{"cause": "bank_fee", "explanation": "Record {{record_id}} is a small bank service charge with no GL counterpart.", "confidence": 0.85}}'
    ),
}
