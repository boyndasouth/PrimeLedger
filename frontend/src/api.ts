const BASE = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

// ── wake-up banner ─────────────────────────────────────────────────────────
let wakeupTimer: ReturnType<typeof setTimeout> | null = null
let _setWakingUp: ((v: boolean) => void) | null = null

export function registerWakeupSetter(fn: (v: boolean) => void) {
  _setWakingUp = fn
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  wakeupTimer = setTimeout(() => {
    _setWakingUp?.(true)
  }, 3000)

  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...init?.headers },
      ...init,
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`${res.status} ${res.statusText}: ${text}`)
    }
    return res.json() as Promise<T>
  } finally {
    clearTimeout(wakeupTimer)
    _setWakingUp?.(false)
  }
}

async function requestForm<T>(path: string, body: FormData): Promise<T> {
  wakeupTimer = setTimeout(() => {
    _setWakingUp?.(true)
  }, 3000)

  try {
    const res = await fetch(`${BASE}${path}`, { method: 'POST', body })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`${res.status} ${res.statusText}: ${text}`)
    }
    return res.json() as Promise<T>
  } finally {
    clearTimeout(wakeupTimer)
    _setWakingUp?.(false)
  }
}

// ── types ──────────────────────────────────────────────────────────────────

export type RunStatus = 'pending' | 'running' | 'done' | 'failed'
export type ExceptionCause = 'timing' | 'bank_fee' | 'duplicate' | 'fx_variance' | 'missing_invoice' | 'other'
export type ExceptionStatus = 'open' | 'approved' | 'edited' | 'rejected'
export type MatchStatus = 'auto_accepted' | 'needs_review' | 'approved' | 'rejected'
export type Decision = 'approve' | 'edit' | 'reject'

export interface Run {
  id: string
  created_at: string
  period_start: string
  period_end: string
  status: RunStatus
  bank_count: number
  gl_count: number
  started_at: string | null
  finished_at: string | null
}

export interface Match {
  id: string
  run_id: string
  match_type: 'exact' | 'fuzzy' | 'one_to_many' | 'many_to_one'
  confidence: number
  bank_ids: string[]
  gl_ids: string[]
  reasoning: string
  status: MatchStatus
}

export interface Exception {
  id: string
  run_id: string
  side: 'bank' | 'gl'
  record_ids: string[]
  cause: ExceptionCause
  explanation: string
  llm_confidence: number
  status: ExceptionStatus
}

export interface JournalLine {
  account: string
  debit: string | null
  credit: string | null
  memo: string
}

export interface JournalDraft {
  id: string
  exception_id: string
  lines: JournalLine[]
  drafted_by: string
  final_lines: JournalLine[] | null
  decided_by: string | null
  decided_at: string | null
  decision: Decision | null
  decision_note: string | null
}

// Richer type returned by GET /api/exceptions/{id} — matches backend ExceptionDetailOut
export interface BankTxnRecord {
  id: string
  source_txn_id: string
  date: string
  description_raw: string
  payee_normalized: string | null
  amount: string
  currency: string
  amount_usd: string
}

export interface GLEntryRecord {
  id: string
  source_entry_id: string
  date: string
  account: string
  payee_raw: string | null
  payee_normalized: string | null
  memo: string | null
  amount: string
  currency: string
  amount_usd: string
  reference: string | null
}

export interface ExceptionDetail {
  exception: Exception
  bank_records: BankTxnRecord[]
  gl_records: GLEntryRecord[]
  journal_draft: JournalDraft | null
}

export interface AuditRow {
  seq: number
  ts: string
  actor: string
  action: string
  entity_type: string
  entity_id: string
  payload: Record<string, unknown>
  prev_hash: string
  hash: string
}

export interface VerifyResult {
  valid: boolean
  broken_at_seq: number | null
}

export interface Metrics {
  auto_match_rate: number
  match_precision: number
  match_recall: number
  cause_accuracy: number
  pipeline_seconds: Record<string, number>
  manual_baseline_minutes: number
  audit_complete: boolean
}

// ── endpoints ──────────────────────────────────────────────────────────────

export const api = {
  health: () => request<{ status: string }>('/health'),

  createRun: (bankFile: File, glFile: File, periodStart: string, periodEnd: string) => {
    const fd = new FormData()
    fd.append('bank_file', bankFile)
    fd.append('gl_file', glFile)
    fd.append('period_start', periodStart)
    fd.append('period_end', periodEnd)
    return requestForm<Run>('/api/runs', fd)
  },

  getRun: (id: string) => request<Run>(`/api/runs/${id}`),
  getMatches: (runId: string, status?: MatchStatus) =>
    request<Match[]>(`/api/runs/${runId}/matches${status ? `?status=${status}` : ''}`),
  getExceptions: (runId: string, status?: ExceptionStatus) =>
    request<Exception[]>(`/api/runs/${runId}/exceptions${status ? `?status=${status}` : ''}`),

  getException: (id: string) => request<ExceptionDetail>(`/api/exceptions/${id}`),
  decide: (id: string, payload: { decision: Decision; user_id: string; final_lines?: JournalLine[]; note?: string }) =>
    request<JournalDraft>(`/api/exceptions/${id}/decision`, { method: 'POST', body: JSON.stringify(payload) }),

  getAudit: (runId: string) => request<AuditRow[]>(`/api/runs/${runId}/audit`),
  verifyChain: () => request<VerifyResult>('/api/audit/verify'),

  getMetrics: (runId: string) => request<Metrics>(`/api/runs/${runId}/metrics`),

  seedDemo: () => request<{ run_id: string }>('/api/demo/seed', { method: 'POST' }),
}
