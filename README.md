# PrimeLedger

**AI-assisted bank-to-ledger reconciliation.** PrimeLedger ingests a bank statement CSV and a general-ledger export, runs a four-agent pipeline to match transactions and explain exceptions, then gives an accountant a short review queue. Agents propose; people approve. Every decision is written to a tamper-evident audit log.

Built for the **IBM SkillsBuild AI Experiential Learning Lab** with IBM watsonx.ai, watsonx Orchestrate and IBM Bob.

---

## Demo story

1. Upload a bank statement CSV and a GL export CSV for one month — or click **Load demo data** on the Upload screen to use the committed synthetic dataset.
2. Run reconciliation and see the auto-match rate and the exception queue.
3. Open an exception: read the plain-language cause and the drafted journal entry.
4. Approve one, edit one, reject one.
5. Open the audit trail and verify the hash chain is intact.
6. See match accuracy, auto-match rate and pipeline timing on the Metrics screen.

---

## How it works

| Agent | Function | What it does |
|---|---|---|
| Ingestion | `ingest(run_id, bank_csv, gl_csv)` | Parses and validates both CSVs, normalises dates, amounts, currencies and payee names, converts to USD with a fixed FX table |
| Matching | `match(run_id)` | Exact → fuzzy 1:1 → many-to-one / one-to-many, each with a confidence score |
| Investigation | `investigate(run_id)` | Rule-based hints plus a watsonx.ai call to classify each unmatched item (timing, bank fee, duplicate, FX variance, missing invoice) and explain it |
| Resolution | `resolve(run_id)` | Drafts a balanced journal entry per exception; accounts and amounts come from code, the LLM writes only the memo |

Confidence ≥ 0.90 is auto-accepted, 0.60–0.90 goes to review, below 0.60 is unmatched.

The agents run in sequence through `backend/app/agents/pipeline.py`, or one stage at a time through the `/api/runs/{id}/<stage>` endpoints so watsonx Orchestrate can coordinate them.

### Responsible AI

- **Human in the loop** — no journal entry is final without an accountant's decision.
- **Confidence thresholds** — anything below the auto-accept threshold goes to the review queue.
- **Segregation of duties** — a `preparer` cannot approve entries.
- **Explainability** — every match and exception stores its reasoning and source record IDs.
- **Data masking** — account numbers, IBANs and routing numbers are masked before any text reaches a prompt.
- **Audit trail** — append-only log with a SHA-256 hash chain; `GET /api/audit/verify` detects tampering.
- **Synthetic data only** in this prototype.

---

## Stack

| Layer | Technology | Hosting |
|---|---|---|
| Frontend | React 19, Vite, TypeScript, Tailwind CSS, React Router | Vercel |
| Backend | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 | Render |
| Database | SQLite (local) / Postgres (production) | Neon or Supabase |
| Matching | pandas, rapidfuzz | — |
| LLM | IBM watsonx.ai (`ibm-watsonx-ai` SDK, Granite instruct model) | IBM Cloud |
| Agent coordination | IBM watsonx Orchestrate | IBM Cloud |
| Tests | pytest | — |

---

## Getting started

### Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer

### Backend

Windows (PowerShell):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

macOS / Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

API: http://localhost:8000 · interactive docs: http://localhost:8000/docs

The default `.env` uses SQLite and `LLM_MODE=mock`, so the app runs without any IBM credentials.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env      # Windows: Copy-Item .env.example .env
npm run dev
```

App: http://localhost:5173

---

## Environment variables

### Backend — `backend/.env`

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./primeledger.db` | SQLAlchemy connection string (Postgres URL in production) |
| `FRONTEND_ORIGINS` | `http://localhost:5173` | Allowed CORS origins, comma-separated |
| `LLM_MODE` | `mock` | `mock` returns canned responses; `live` calls watsonx.ai |
| `WATSONX_API_KEY` | — | IBM Cloud API key |
| `WATSONX_PROJECT_ID` | — | watsonx.ai project ID |
| `WATSONX_URL` | `https://us-south.ml.cloud.ibm.com` | Regional watsonx.ai endpoint |
| `WATSONX_MODEL_ID` | — | Instruct model ID (copy from Prompt Lab → View code) |

### Frontend — `frontend/.env`

| Variable | Default | Description |
|---|---|---|
| `VITE_API_URL` | `http://localhost:8000` | Backend base URL |

Never commit `.env` files; only the `.env.example` templates belong in git.

---

## Running tests

From `backend/` with the virtual environment active:

```powershell
$env:LLM_MODE="mock"
python -m pytest
```

Use `python -m pytest` rather than `pytest` so the `app` package is importable. Mock mode means the tests use no watsonx.ai tokens.

### Synthetic data

```bash
python data/generate_synthetic.py --seed 42 --rows 300
```

Writes `bank.csv`, `gl.csv` and `labels.json` (ground truth for the metrics) to `backend/data/samples/`.

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/api/runs` | Upload CSVs and run the full pipeline |
| GET | `/api/runs/{id}` | Run status, counts and stage timings |
| GET | `/api/runs/{id}/matches` | Matches (filter by `status`) |
| GET | `/api/runs/{id}/exceptions` | Exception queue (filter by `status`) |
| GET | `/api/exceptions/{id}` | Exception with source records and journal draft |
| POST | `/api/exceptions/{id}/decision` | Approve, edit or reject a draft |
| GET | `/api/runs/{id}/audit` | Audit rows for a run |
| GET | `/api/audit/verify` | Hash-chain integrity check |
| GET | `/api/runs/{id}/metrics` | Accuracy and timing metrics |
| POST | `/api/demo/seed` | Load the committed sample data and run it |
| POST | `/api/runs/{id}/ingest` | Orchestrate tool — Ingestion agent only |
| POST | `/api/runs/{id}/match` | Orchestrate tool — Matching agent only |
| POST | `/api/runs/{id}/investigate` | Orchestrate tool — Investigation agent only |
| POST | `/api/runs/{id}/resolve` | Orchestrate tool — Resolution agent only |

Full schemas are at `/docs` and `/openapi.json` on a running backend.

---

## Deployment

### Backend on Render

- Root directory: `backend`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment: set every backend variable above, with `DATABASE_URL` pointing at Postgres and `FRONTEND_ORIGINS` including the Vercel URL.

The free tier sleeps after about 15 minutes of inactivity. Open `/health` a few minutes before a demo.

### Frontend on Vercel

- Root directory: `frontend`
- Framework preset: Vite
- Environment: `VITE_API_URL` = the Render service URL

---

## Repository layout

```
PrimeLedger/
  SPEC.md                   source of truth for the build
  .bob/rules/               project rules for IBM Bob
  backend/
    app/
      main.py               FastAPI app, CORS, routers, /health
      config.py             settings, FX table, thresholds, chart of accounts
      db.py · models.py · schemas.py
      routes/               runs, exceptions, audit, metrics, orchestrate
      agents/               ingestion, matching, investigation, resolution, pipeline
      llm/                  watsonx_client (sole watsonx caller), prompts, masking
      audit/log.py          append-only hash-chained audit log
    data/
      generate_synthetic.py
      samples/              committed bank.csv, gl.csv, labels.json
    tests/
    requirements.txt
    .env.example
  frontend/
    src/
      api.ts                all backend calls
      pages/                Upload, Queue, ExceptionDetail, Audit, Metrics
    .env.example
```

---

## Built with IBM Bob

The code was built step by step with IBM Bob against `SPEC.md`. `.bob/rules/01-project.md` holds the project rules Bob loads in every task: stack, file scope, money as `Decimal`, masking, and the append-only audit log.
