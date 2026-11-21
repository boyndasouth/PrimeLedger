# PrimeLedger — Build Spec

> Source of truth for building the prototype. When implementing anything, follow this file. If something here conflicts with a prompt, ask before deviating.

## 1. Summary

PrimeLedger reconciles bank transactions against general-ledger (GL) entries using four agents in sequence — **Ingestion → Matching → Investigation → Resolution** — and presents an accountant with a short queue of explained exceptions to approve, edit or reject. Every decision is written to a tamper-evident audit log.

**Primary demo user:** a controller at a mid-size company reconciling one operating bank account for one month (USD primary, some EUR/GBP lines to exercise FX).

**Demo story (must work end to end):**
1. Upload a bank statement CSV and a GL export CSV for one month.
2. Run reconciliation; see the auto-match rate and a queue of exceptions.
3. Open an exception; read the plain-language cause and the drafted journal entry.
4. Approve one, edit one, reject one.
5. Open the audit trail and verify the hash chain is intact.
6. See metrics against the labeled test set (accuracy, auto-match rate, time taken).

## 2. Stack

| Layer | Choice | Host |
|---|---|---|
| Frontend | React + Vite + TypeScript, Tailwind CSS | Vercel |
| Backend | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2 | Render (web service) |
| Database | Postgres (SQLite locally for dev) | Neon or Supabase free tier |
| Matching | pandas, rapidfuzz | — |
| LLM | watsonx.ai via `ibm-watsonx-ai` SDK | IBM Cloud |
| Tests | pytest (backend), Vitest (frontend, optional) | — |

Do not add other frameworks or services without asking.

## 3. Repository layout

```
primeledger/
  SPEC.md
  backend/
    app/
      main.py              # FastAPI app, CORS, router registration, /health
      config.py            # env settings (pydantic-settings)
      db.py                # engine, session, Base
      models.py            # SQLAlchemy tables (section 5)
      schemas.py           # Pydantic request/response models
      routes/
        runs.py            # upload + run reconciliation
        exceptions.py      # exception queue + decisions
        audit.py           # audit log + verify
        metrics.py
      agents/
        ingestion.py
        matching.py
        investigation.py
        resolution.py
        pipeline.py        # runs the 4 agents in order
      llm/
        watsonx_client.py  # single place that calls watsonx.ai
        prompts.py
        masking.py         # account-number masking
      audit/
        log.py             # append + hash chain + verify
    data/
      generate_synthetic.py
      samples/             # generated CSVs + labels.json (committed)
    tests/
    requirements.txt
    .env.example
  frontend/
    src/
      api.ts               # all fetch calls; base URL from VITE_API_URL
      pages/  Upload.tsx  Queue.tsx  ExceptionDetail.tsx  Audit.tsx  Metrics.tsx
      components/
    .env.example
```

## 4. Input formats

### 4.1 Bank statement CSV
`txn_id, date, description, amount, currency, balance(optional)`
- `amount`: signed decimal; deposits positive, withdrawals negative.
- `description`: raw bank text, may be truncated (e.g. `AMZN MKTP US*2K4`).

### 4.2 GL export CSV
`entry_id, date, account, payee, memo, amount, currency, reference(optional)`
- Same sign convention as the bank side (cash account perspective).

### 4.3 Out of scope for MVP
BAI2 and PDF statements. Leave a `parse_bai2` / `parse_pdf` stub raising `NotImplementedError` so the design shows where they plug in.

## 5. Data model

All money stored as `Numeric(14,2)`; never floats. All IDs are UUID strings unless noted.

**runs** — `id, created_at, period_start, period_end, status (pending|running|done|failed), bank_count, gl_count, started_at, finished_at`

**bank_txns** — `id, run_id, source_txn_id, date, description_raw, payee_normalized, amount, currency, amount_usd`

**gl_entries** — `id, run_id, source_entry_id, date, account, payee_raw, payee_normalized, memo, amount, currency, amount_usd, reference`

**matches** — `id, run_id, match_type (exact|fuzzy|one_to_many|many_to_one), confidence (0–1), bank_ids (JSON list), gl_ids (JSON list), reasoning, status (auto_accepted|needs_review|approved|rejected)`

**exceptions** — `id, run_id, side (bank|gl), record_ids (JSON list), cause (timing|bank_fee|duplicate|fx_variance|missing_invoice|other), explanation, llm_confidence, status (open|approved|edited|rejected)`

**journal_drafts** — `id, exception_id, lines (JSON: [{account, debit, credit, memo}]), drafted_by ("resolution_agent"), final_lines (JSON, nullable), decided_by, decided_at, decision (approve|edit|reject), decision_note`

**users** (seeded, no auth UI) — `id, name, role (preparer|approver)`

**audit_log** — `seq (int, autoincrement), ts, actor (agent name or user id), action, entity_type, entity_id, payload (JSON), prev_hash, hash`

## 6. Agents

Each agent is a plain function with typed input/output so it can be tested alone and later wrapped as a watsonx Orchestrate tool. Each agent writes its actions to the audit log.

### 6.1 Ingestion Agent — `ingest(run_id, bank_csv, gl_csv) -> IngestResult`
- Parse both CSVs; reject with a clear 422 error on missing columns.
- Normalize dates to ISO, amounts to Decimal, currency to ISO 4217.
- Convert to `amount_usd` using a fixed FX table in `config.py` (deterministic for the demo).
- `payee_normalized`: uppercase, strip card/processor noise (`*`, trailing IDs, `POS`, `ACH`, `DEBIT`), map known aliases from a small dictionary (e.g. `AMZN` → `AMAZON`). No LLM here.

### 6.2 Matching Agent — `match(run_id) -> MatchResult`
Deterministic first, in this order; each record can be matched once.
1. **Exact:** same `amount`, same currency, date within ±1 day, payee similarity ≥ 90. Confidence 0.99.
2. **Fuzzy 1:1:** same amount, date within ±5 days, rapidfuzz `token_set_ratio` on payee ≥ 70. Confidence = weighted score of payee similarity (0.6) and date closeness (0.4).
3. **Many-to-one / one-to-many:** for remaining items, search subsets of up to 3 records on one side within ±5 days whose sum equals a single record on the other side (exact to the cent). Confidence 0.85, lowered if payees differ.
4. Optional LLM tiebreak: only for candidate pairs with confidence 0.55–0.75, ask watsonx "same payee? yes/no + reason". Cap at 20 calls per run.

Thresholds (in `config.py`): `AUTO_ACCEPT ≥ 0.90`, `NEEDS_REVIEW 0.60–0.90`, below 0.60 → unmatched.

### 6.3 Investigation Agent — `investigate(run_id) -> list[Exception]`
For each unmatched record:
- Run cheap heuristics first and pass the results to the LLM as hints:
  - **bank_fee:** bank-side only, small negative amount, description contains FEE/CHG/SERVICE.
  - **duplicate:** same amount + payee + date within 2 days as another record on the same side.
  - **timing:** a matching counterpart exists just outside the period or outside the date window.
  - **fx_variance:** a counterpart differs by < 3% and currencies differ.
  - **missing_invoice:** bank deposit/withdrawal with no GL counterpart and no other signal.
- Call watsonx with the masked record, the nearest 3 candidates and the hints. Response must be JSON: `{"cause": "...", "explanation": "...", "confidence": 0.0}`. Explanation ≤ 2 sentences, plain English, cites the records used.
- If the LLM fails or returns invalid JSON: retry once, then fall back to the top heuristic with `confidence 0.5` and explanation "Heuristic classification; model unavailable."

### 6.4 Resolution Agent — `resolve(run_id) -> list[JournalDraft]`
- Draft a balanced journal entry per exception (debits = credits, checked in code; reject and re-draft if unbalanced).
- Use a fixed chart of accounts in `config.py`: `1000 Cash`, `6100 Bank Fees`, `7900 FX Gain/Loss`, `2000 Accounts Payable`, `1200 Accounts Receivable`, `9999 Suspense`.
- Templates per cause (bank_fee → Dr 6100 / Cr 1000; fx_variance → 7900; duplicate → reverse the duplicate GL entry; timing → no entry, note "clears next period"; missing_invoice → 9999 Suspense with a note to obtain the invoice). The LLM writes only the memo text; account selection and amounts come from code.
- Nothing posts automatically. All drafts start `open`.

### 6.5 Pipeline — `run_pipeline(run_id)`
Runs 6.1 → 6.4, records timings per stage, sets run status. Runs synchronously for the demo (sample data is small); keep it a single function so it can move to a background task later.

## 7. watsonx.ai integration

- Only `llm/watsonx_client.py` talks to watsonx. Expose `generate_json(prompt: str, schema_hint: str) -> dict`.
- Env vars: `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_URL` (e.g. `https://us-south.ml.cloud.ibm.com`), `WATSONX_MODEL_ID` (configurable; use an instruct model available in the project, e.g. a Granite instruct model).
- Params: greedy decoding / temperature 0, `max_new_tokens` 300.
- Timeout 20s, one retry.
- `LLM_MODE=mock` env flag returns canned responses so tests and frontend work without credentials or coins.
- **Masking (required):** before any prompt, `masking.py` replaces digit runs of 6+ with `****` + last 4, and removes anything matching an IBAN/routing pattern. Unit-test this.

## 8. Audit log

- Append-only. No update/delete code paths for `audit_log`.
- `hash = sha256(prev_hash + canonical_json({seq, ts, actor, action, entity_type, entity_id, payload}))`; first row uses `prev_hash = "GENESIS"`.
- Actions to log at minimum: `run.created`, `ingest.completed`, `match.created`, `exception.created`, `journal.drafted`, `journal.approved|edited|rejected`.
- `GET /audit/verify` recomputes the chain and returns `{valid: bool, broken_at_seq: int|null}`.

## 9. Responsible-AI rules (enforced in code)

- **Human in the loop:** no journal is final without a user decision.
- **Confidence thresholds:** anything < `AUTO_ACCEPT` goes to review.
- **Segregation of duties:** the decision endpoint rejects with 403 if `decided_by` has role `preparer` or equals the user who created an override for that item.
- **Explainability:** every match and exception stores its `reasoning`/`explanation` and source record IDs.
- **Data protection:** synthetic data only; masking on every prompt and on any LLM output shown in the UI.

## 10. API

Base path `/api`. JSON everywhere except the upload.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{status: "ok"}` (not under `/api`) |
| POST | `/api/runs` | multipart: `bank_file`, `gl_file`, `period_start`, `period_end`; creates run and executes pipeline; returns run summary |
| GET | `/api/runs/{id}` | run status, counts, stage timings |
| GET | `/api/runs/{id}/matches?status=` | matches list |
| GET | `/api/runs/{id}/exceptions?status=` | exception queue |
| GET | `/api/exceptions/{id}` | exception + source records + journal draft |
| POST | `/api/exceptions/{id}/decision` | `{decision: approve|edit|reject, user_id, final_lines?, note?}` |
| GET | `/api/runs/{id}/audit` | audit rows for run |
| GET | `/api/audit/verify` | chain integrity |
| GET | `/api/runs/{id}/metrics` | metrics (section 12) |
| POST | `/api/demo/seed` | loads the committed sample CSVs and runs them (one-click demo) |

CORS: allow origins from `FRONTEND_ORIGINS` env (comma-separated), plus `http://localhost:5173` in dev.

## 11. Frontend screens

1. **Upload** — two file inputs, period dates, "Run reconciliation", and a "Load demo data" button calling `/api/demo/seed`.
2. **Queue** — summary cards (total, auto-matched %, needs review, exceptions); table of exceptions with cause badge, amount, confidence, status; filter by cause/status.
3. **Exception detail** — source records side by side, the explanation, the drafted journal lines (editable table that must balance before save), Approve / Save edits / Reject, user picker (seeded users).
4. **Audit** — chronological log, "Verify chain" button showing valid/invalid.
5. **Metrics** — the numbers from section 12, plus a before/after time comparison.

Plain, clean finance-tool look. Show a "Backend waking up…" message if the first request takes > 3s (Render free tier cold start).

## 12. Synthetic data and metrics

`data/generate_synthetic.py --seed 42 --rows 300` writes `bank.csv`, `gl.csv`, `labels.json`.
- ~70% clean exact matches, ~10% fuzzy payee variants, ~5% split/combined payments, and ~15% exceptions spread across all five causes.
- `labels.json` records the true pairing and true cause for each record.
- Committed sample output in `data/samples/` so the demo is deterministic.

`/metrics` returns:
- `auto_match_rate` = auto-accepted matched records / total records
- `match_precision`, `match_recall` vs labels
- `cause_accuracy` = correct exception causes / labeled exceptions
- `pipeline_seconds` per stage and total
- `manual_baseline_minutes` (config constant, e.g. 2 min per record — state the assumption in the UI)
- `audit_complete` = every record has ≥ 1 audit row

## 13. Configuration

`backend/.env.example`
```
DATABASE_URL=sqlite:///./primeledger.db
FRONTEND_ORIGINS=http://localhost:5173
LLM_MODE=mock
WATSONX_API_KEY=
WATSONX_PROJECT_ID=
WATSONX_URL=https://us-south.ml.cloud.ibm.com
WATSONX_MODEL_ID=
```
`frontend/.env.example`
```
VITE_API_URL=http://localhost:8000
```

## 14. Deployment

- **Render:** root `backend/`, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set env vars in the dashboard. Create tables on startup (`Base.metadata.create_all`) — no migrations for the prototype.
- **Vercel:** root `frontend/`, framework Vite, set `VITE_API_URL` to the Render URL.
- Add the Vercel URL to `FRONTEND_ORIGINS` on Render.
- Before the demo: hit `/health` to wake the backend.

## 15. Testing (minimum)

- `test_masking.py` — account numbers never survive masking.
- `test_matching.py` — exact, fuzzy, and split cases from a tiny fixture.
- `test_resolution.py` — every draft balances.
- `test_audit.py` — chain verifies; tampering with one row is detected.
- `test_pipeline.py` — full run on sample data with `LLM_MODE=mock`.
- `test_sod.py` — preparer cannot approve.

## 16. Build order (one Bob task each)

1. Synthetic data generator + committed samples.
2. `models.py`, `db.py`, `config.py`, audit log + tests.
3. Ingestion agent + tests.
4. Matching agent + tests.
5. watsonx client, masking, mock mode + tests.
6. Investigation agent.
7. Resolution agent + balance tests.
8. Pipeline + API routes + `/demo/seed`.
9. Frontend: api.ts, Upload, Queue.
10. Frontend: Exception detail, Audit, Metrics.
11. Deploy and fix CORS/env issues.

## 17. Out of scope

Real bank connections, authentication/login, multi-tenant clients, BAI2/PDF parsing, posting to a real ERP, multiple bank accounts per run, background job queues.
