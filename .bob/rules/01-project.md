# PrimeLedger project rules

- SPEC.md at the repo root is the source of truth. Read only the section(s) the task names; do not re-read the whole spec unless asked.
- Stack is fixed: FastAPI + Pydantic v2 + SQLAlchemy 2 (backend/), React + Vite + TypeScript + Tailwind (frontend/). Do not add frameworks or services without asking.
- Only create or edit the files the task names. If another file must change, say which and why before changing it.
- Money is Decimal / Numeric(14,2), never float.
- Only backend/app/llm/watsonx_client.py may call watsonx.ai. Default to LLM_MODE=mock in tests.
- Mask account numbers (backend/app/llm/masking.py) before any text reaches a prompt.
- Never write code that updates or deletes audit_log rows.
- Every new backend module gets a pytest test in backend/tests/.
- Keep answers short: show the changed code and a one-line summary, no long explanations.
