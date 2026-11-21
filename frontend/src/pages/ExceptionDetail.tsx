import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  type BankTxnRecord,
  type Decision,
  type ExceptionCause,
  type ExceptionStatus,
  type ExceptionDetail as ExceptionDetailType,
  type GLEntryRecord,
  type JournalLine,
  api,
} from '../api'

// ── seeded users (matches backend seed_users) ─────────────────────────────
const SEEDED_USERS = [
  { id: 'user-preparer-1', name: 'Alice Preparer', role: 'preparer' as const },
  { id: 'user-approver-1', name: 'Bob Approver', role: 'approver' as const },
  { id: 'user-approver-2', name: 'Carol Approver', role: 'approver' as const },
]

// ── badge helpers ─────────────────────────────────────────────────────────

const CAUSE_STYLES: Record<ExceptionCause, string> = {
  timing:          'bg-sky-100 text-sky-800',
  bank_fee:        'bg-amber-100 text-amber-800',
  duplicate:       'bg-orange-100 text-orange-800',
  fx_variance:     'bg-violet-100 text-violet-800',
  missing_invoice: 'bg-rose-100 text-rose-800',
  other:           'bg-gray-100 text-gray-700',
}

const CAUSE_LABELS: Record<ExceptionCause, string> = {
  timing:          'Timing',
  bank_fee:        'Bank fee',
  duplicate:       'Duplicate',
  fx_variance:     'FX variance',
  missing_invoice: 'Missing invoice',
  other:           'Other',
}

const STATUS_STYLES: Record<ExceptionStatus, string> = {
  open:     'bg-yellow-50 text-yellow-700 ring-1 ring-yellow-200',
  approved: 'bg-green-50 text-green-700 ring-1 ring-green-200',
  edited:   'bg-blue-50 text-blue-700 ring-1 ring-blue-200',
  rejected: 'bg-red-50 text-red-700 ring-1 ring-red-200',
}

function CauseBadge({ cause }: { cause: ExceptionCause }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${CAUSE_STYLES[cause]}`}>
      {CAUSE_LABELS[cause]}
    </span>
  )
}

function StatusBadge({ status }: { status: ExceptionStatus }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLES[status]}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  )
}

// ── money helpers ─────────────────────────────────────────────────────────

function fmtMoney(val: string | null | undefined): string {
  if (val == null) return '—'
  const n = parseFloat(val)
  return isNaN(n) ? '—' : n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function parseDecimal(val: string | null | undefined): number {
  if (val == null || val === '') return 0
  const n = parseFloat(val)
  return isNaN(n) ? 0 : n
}

// ── journal lines table ───────────────────────────────────────────────────

function JournalTable({
  lines,
  editable,
  onChange,
}: {
  lines: JournalLine[]
  editable: boolean
  onChange?: (lines: JournalLine[]) => void
}) {
  const totalDebit  = lines.reduce((s, l) => s + parseDecimal(l.debit),  0)
  const totalCredit = lines.reduce((s, l) => s + parseDecimal(l.credit), 0)
  const balanced = Math.abs(totalDebit - totalCredit) < 0.005

  function update(idx: number, field: keyof JournalLine, value: string) {
    const next = lines.map((l, i) =>
      i === idx ? { ...l, [field]: value === '' ? null : value } : l,
    )
    onChange?.(next)
  }

  function addLine() {
    onChange?.([...lines, { account: '', debit: null, credit: null, memo: '' }])
  }

  function removeLine(idx: number) {
    onChange?.(lines.filter((_, i) => i !== idx))
  }

  const cellCls = 'px-3 py-2 text-sm'
  const inputCls =
    'w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'

  return (
    <div>
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide w-28">Account</th>
              <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wide w-28">Debit</th>
              <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wide w-28">Credit</th>
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">Memo</th>
              {editable && <th className="px-3 py-2 w-8" />}
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-100">
            {lines.map((line, i) => (
              <tr key={i}>
                <td className={cellCls}>
                  {editable ? (
                    <input
                      className={inputCls}
                      value={line.account}
                      onChange={e => update(i, 'account', e.target.value)}
                      placeholder="e.g. 6100"
                    />
                  ) : (
                    <span className="font-mono">{line.account}</span>
                  )}
                </td>
                <td className={`${cellCls} text-right`}>
                  {editable ? (
                    <input
                      className={`${inputCls} text-right`}
                      value={line.debit ?? ''}
                      onChange={e => update(i, 'debit', e.target.value)}
                      placeholder="0.00"
                    />
                  ) : (
                    <span className="tabular-nums">{line.debit != null ? fmtMoney(line.debit) : ''}</span>
                  )}
                </td>
                <td className={`${cellCls} text-right`}>
                  {editable ? (
                    <input
                      className={`${inputCls} text-right`}
                      value={line.credit ?? ''}
                      onChange={e => update(i, 'credit', e.target.value)}
                      placeholder="0.00"
                    />
                  ) : (
                    <span className="tabular-nums">{line.credit != null ? fmtMoney(line.credit) : ''}</span>
                  )}
                </td>
                <td className={cellCls}>
                  {editable ? (
                    <input
                      className={inputCls}
                      value={line.memo}
                      onChange={e => update(i, 'memo', e.target.value)}
                      placeholder="Memo"
                    />
                  ) : (
                    <span className="text-gray-600">{line.memo}</span>
                  )}
                </td>
                {editable && (
                  <td className={cellCls}>
                    <button
                      onClick={() => removeLine(i)}
                      className="text-gray-400 hover:text-red-500 text-xs"
                      title="Remove line"
                    >
                      ✕
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
          <tfoot className="bg-gray-50 border-t border-gray-200">
            <tr>
              <td className="px-3 py-2 text-xs font-medium text-gray-500">Total</td>
              <td className="px-3 py-2 text-right tabular-nums text-sm font-semibold text-gray-800">
                {fmtMoney(String(totalDebit.toFixed(2)))}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-sm font-semibold text-gray-800">
                {fmtMoney(String(totalCredit.toFixed(2)))}
              </td>
              <td className="px-3 py-2" colSpan={editable ? 2 : 1}>
                {balanced ? (
                  <span className="text-xs text-green-700 font-medium">✓ Balanced</span>
                ) : (
                  <span className="text-xs text-red-600 font-medium">
                    ✗ Unbalanced — difference {Math.abs(totalDebit - totalCredit).toFixed(2)}
                  </span>
                )}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      {editable && (
        <button
          onClick={addLine}
          className="mt-2 text-xs text-blue-600 hover:text-blue-800 underline"
        >
          + Add line
        </button>
      )}
    </div>
  )
}

// ── source record panels ──────────────────────────────────────────────────

function RecordPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">{title}</h3>
      {children}
    </div>
  )
}

function KV({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 text-sm py-0.5">
      <span className="text-gray-400 w-32 shrink-0">{label}</span>
      <span className="text-gray-900 font-medium break-all">{value}</span>
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────

export default function ExceptionDetail() {
  const { id } = useParams<{ id: string }>()

  // Data
  const [exception, setException]     = useState<ExceptionDetailType['exception'] | null>(null)
  const [bankRecords, setBankRecords] = useState<BankTxnRecord[]>([])
  const [glRecords, setGLRecords]     = useState<GLEntryRecord[]>([])
  const [draft, setDraft]             = useState<ExceptionDetailType['journal_draft']>(null)
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState<string | null>(null)

  // Decision state
  const [selectedUser, setSelectedUser] = useState(SEEDED_USERS[0].id)
  const [editMode, setEditMode]         = useState(false)
  const [editLines, setEditLines]       = useState<JournalLine[]>([])
  const [note, setNote]                 = useState('')
  const [submitting, setSubmitting]     = useState(false)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const [forbiddenMsg, setForbiddenMsg]   = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    api.getException(id)
      .then(detail => {
        setException(detail.exception)
        setBankRecords(detail.bank_records ?? [])
        setGLRecords(detail.gl_records ?? [])
        setDraft(detail.journal_draft ?? null)
        if (detail.journal_draft?.lines) setEditLines(detail.journal_draft.lines)
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [id])

  async function submitDecision(decision: Decision) {
    if (!id) return
    setSubmitting(true)
    setDecisionError(null)
    setForbiddenMsg(null)
    try {
      const payload: Parameters<typeof api.decide>[1] = {
        decision,
        user_id: selectedUser,
        note: note || undefined,
      }
      if (decision === 'edit') {
        payload.final_lines = editLines
      }
      await api.decide(id, payload)
      // Reload to reflect updated status
      const detail = await api.getException(id)
      setException(detail.exception)
      setDraft(detail.journal_draft ?? null)
      setEditMode(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Request failed'
      if (msg.includes('403')) {
        setForbiddenMsg('403 Forbidden — Preparers cannot approve, edit, or reject journal entries. Select an approver account.')
      } else {
        setDecisionError(msg)
      }
    } finally {
      setSubmitting(false)
    }
  }

  // Balanced check for save-edits button
  const totalDebit  = editLines.reduce((s, l) => s + parseDecimal(l.debit),  0)
  const totalCredit = editLines.reduce((s, l) => s + parseDecimal(l.credit), 0)
  const isBalanced  = Math.abs(totalDebit - totalCredit) < 0.005

  const isDecided = exception?.status !== 'open'

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-sm text-gray-500">Loading…</div>
    )
  }

  if (error || !exception) {
    return (
      <div className="max-w-4xl mx-auto mt-12 px-4">
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
          {error ?? 'Exception not found.'}
        </p>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      {/* Back link */}
      <Link
        to={`/queue/${exception.run_id}`}
        className="text-sm text-blue-600 hover:underline inline-flex items-center gap-1 mb-5"
      >
        ← Back to queue
      </Link>

      {/* Header */}
      <div className="flex flex-wrap items-start gap-3 mb-6">
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-semibold text-gray-900 flex items-center gap-2">
            Exception
            <span className="font-mono text-base text-gray-400">{exception.id.slice(0, 8)}…</span>
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Run <span className="font-mono text-xs">{exception.run_id.slice(0, 8)}…</span> ·{' '}
            Side: <span className="font-medium text-gray-700 capitalize">{exception.side}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CauseBadge cause={exception.cause} />
          <StatusBadge status={exception.status} />
        </div>
      </div>

      {/* Explanation */}
      {exception.explanation && (
        <div className="mb-6 bg-blue-50 border border-blue-200 rounded-lg px-4 py-3">
          <p className="text-xs font-semibold text-blue-700 uppercase tracking-wide mb-1">AI Explanation</p>
          <p className="text-sm text-blue-900">{exception.explanation}</p>
          {exception.llm_confidence != null && (
            <p className="mt-1 text-xs text-blue-500">
              Confidence: {(Number(exception.llm_confidence) * 100).toFixed(0)}%
            </p>
          )}
        </div>
      )}

      {/* Source records side by side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Bank records */}
        <RecordPanel title={`Bank records (${bankRecords.length})`}>
          {bankRecords.length === 0 ? (
            <p className="text-sm text-gray-400">None</p>
          ) : (
            <div className="space-y-3">
              {bankRecords.map(r => (
                <div key={r.id} className="border border-gray-100 rounded p-2 bg-gray-50">
                  <KV label="ID"          value={<span className="font-mono text-xs">{r.source_txn_id}</span>} />
                  <KV label="Date"        value={r.date} />
                  <KV label="Description" value={r.description_raw} />
                  <KV label="Payee"       value={r.payee_normalized ?? '—'} />
                  <KV label="Amount"      value={`${fmtMoney(String(r.amount))} ${r.currency}`} />
                  <KV label="Amount USD"  value={`${fmtMoney(String(r.amount_usd))} USD`} />
                </div>
              ))}
            </div>
          )}
        </RecordPanel>

        {/* GL records */}
        <RecordPanel title={`GL records (${glRecords.length})`}>
          {glRecords.length === 0 ? (
            <p className="text-sm text-gray-400">None</p>
          ) : (
            <div className="space-y-3">
              {glRecords.map(r => (
                <div key={r.id} className="border border-gray-100 rounded p-2 bg-gray-50">
                  <KV label="ID"        value={<span className="font-mono text-xs">{r.source_entry_id}</span>} />
                  <KV label="Date"      value={r.date} />
                  <KV label="Account"   value={r.account} />
                  <KV label="Payee"     value={r.payee_normalized ?? r.payee_raw ?? '—'} />
                  <KV label="Memo"      value={r.memo ?? '—'} />
                  <KV label="Amount"    value={`${fmtMoney(String(r.amount))} ${r.currency}`} />
                  <KV label="Ref"       value={r.reference ?? '—'} />
                </div>
              ))}
            </div>
          )}
        </RecordPanel>
      </div>

      {/* Journal draft */}
      {draft && (
        <div className="mb-6">
          <h2 className="text-base font-semibold text-gray-800 mb-3">
            Drafted journal entry
            <span className="ml-2 text-xs text-gray-400 font-normal">by {draft.drafted_by}</span>
          </h2>

          {editMode ? (
            <JournalTable lines={editLines} editable onChange={setEditLines} />
          ) : (
            <JournalTable lines={draft.lines} editable={false} />
          )}

          {/* Decision area */}
          {isDecided ? (
            <div className="mt-4 bg-gray-50 border border-gray-200 rounded-lg px-4 py-3">
              <p className="text-sm text-gray-600">
                Decision: <span className="font-semibold text-gray-900 capitalize">{draft.decision}</span>
                {draft.decided_by && (
                  <> · by <span className="font-medium">{SEEDED_USERS.find(u => u.id === draft.decided_by)?.name ?? draft.decided_by}</span></>
                )}
                {draft.decided_at && (
                  <> · {new Date(draft.decided_at).toLocaleString()}</>
                )}
              </p>
              {draft.decision_note && (
                <p className="text-xs text-gray-500 mt-1">Note: {draft.decision_note}</p>
              )}
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              {/* User picker */}
              <div className="flex flex-wrap items-center gap-3">
                <label className="text-sm text-gray-600 font-medium shrink-0">Acting as:</label>
                <select
                  value={selectedUser}
                  onChange={e => { setSelectedUser(e.target.value); setForbiddenMsg(null) }}
                  className="text-sm border border-gray-300 rounded-md px-2 py-1.5 text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {SEEDED_USERS.map(u => (
                    <option key={u.id} value={u.id}>{u.name} ({u.role})</option>
                  ))}
                </select>
              </div>

              {/* Optional note */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">Note (optional)</label>
                <input
                  type="text"
                  value={note}
                  onChange={e => setNote(e.target.value)}
                  placeholder="Reason or comment"
                  className="w-full max-w-sm border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              {/* 403 / error messages */}
              {forbiddenMsg && (
                <div className="flex items-start gap-2 bg-red-50 border border-red-300 rounded-lg px-4 py-3">
                  <span className="text-red-600 font-bold text-sm shrink-0">403</span>
                  <p className="text-sm text-red-700">{forbiddenMsg}</p>
                </div>
              )}
              {decisionError && (
                <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
                  {decisionError}
                </p>
              )}

              {/* Action buttons */}
              <div className="flex flex-wrap gap-2">
                <button
                  disabled={submitting}
                  onClick={() => submitDecision('approve')}
                  className="px-4 py-2 rounded-md text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Approve
                </button>

                {!editMode ? (
                  <button
                    onClick={() => setEditMode(true)}
                    className="px-4 py-2 rounded-md text-sm font-medium bg-blue-50 text-blue-700 ring-1 ring-blue-200 hover:bg-blue-100"
                  >
                    Edit journal lines
                  </button>
                ) : (
                  <>
                    <button
                      disabled={!isBalanced || submitting}
                      onClick={() => submitDecision('edit')}
                      title={!isBalanced ? 'Lines must balance before saving' : undefined}
                      className="px-4 py-2 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      Save edits
                    </button>
                    <button
                      onClick={() => { setEditMode(false); setEditLines(draft.lines) }}
                      className="px-4 py-2 rounded-md text-sm font-medium text-gray-600 ring-1 ring-gray-200 hover:bg-gray-50"
                    >
                      Cancel
                    </button>
                  </>
                )}

                <button
                  disabled={submitting}
                  onClick={() => submitDecision('reject')}
                  className="px-4 py-2 rounded-md text-sm font-medium bg-red-50 text-red-700 ring-1 ring-red-200 hover:bg-red-100 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Reject
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {!draft && !loading && (
        <p className="text-sm text-gray-400">No journal draft available for this exception.</p>
      )}
    </div>
  )
}
