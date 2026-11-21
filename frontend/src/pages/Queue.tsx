import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { type Exception, type ExceptionCause, type ExceptionStatus, type Run, api } from '../api'

// ── cause badge colours ────────────────────────────────────────────────────
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

// ── summary card ──────────────────────────────────────────────────────────
function SummaryCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-5 py-4">
      <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-gray-900">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-gray-400">{sub}</p>}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────
export default function Queue() {
  const { runId } = useParams<{ runId: string }>()

  const [run, setRun] = useState<Run | null>(null)
  const [exceptions, setExceptions] = useState<Exception[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // filters
  const [causeFilter, setCauseFilter] = useState<ExceptionCause | ''>('')
  const [statusFilter, setStatusFilter] = useState<ExceptionStatus | ''>('')

  useEffect(() => {
    if (!runId) return
    setLoading(true)
    Promise.all([api.getRun(runId), api.getExceptions(runId)])
      .then(([r, ex]) => {
        setRun(r)
        setExceptions(ex)
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [runId])

  // ── summary stats ────────────────────────────────────────────────────────
  const total = run ? (run.bank_count + run.gl_count) : 0
  const autoMatchedCount = useMemo(
    () => exceptions.filter(e => e.status === 'approved').length,
    [exceptions],
  )
  const autoMatchPct = total > 0 ? Math.round(((total - exceptions.length) / total) * 100) : 0
  const needsReview = exceptions.filter(e => e.status === 'open').length
  const exceptionCount = exceptions.length

  // ── filtered list ────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    return exceptions.filter(e => {
      if (causeFilter && e.cause !== causeFilter) return false
      if (statusFilter && e.status !== statusFilter) return false
      return true
    })
  }, [exceptions, causeFilter, statusFilter])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-sm text-gray-500">
        Loading…
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto mt-12 px-4">
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">{error}</p>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">Exception queue</h1>
        {run && (
          <p className="text-sm text-gray-500 mt-1">
            Run <span className="font-mono text-xs">{run.id.slice(0, 8)}…</span> ·{' '}
            {run.period_start} → {run.period_end} ·{' '}
            <span className={`font-medium ${run.status === 'done' ? 'text-green-700' : run.status === 'failed' ? 'text-red-600' : 'text-amber-600'}`}>
              {run.status}
            </span>
          </p>
        )}
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <SummaryCard label="Total records" value={total} sub={`${run?.bank_count ?? 0} bank + ${run?.gl_count ?? 0} GL`} />
        <SummaryCard label="Auto-matched" value={`${autoMatchPct}%`} sub={`${total - exceptionCount} records`} />
        <SummaryCard label="Needs review" value={needsReview} sub="open exceptions" />
        <SummaryCard label="Exceptions" value={exceptionCount} sub="total flagged" />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <label className="text-sm text-gray-600 font-medium">Filter:</label>
        <select
          value={causeFilter}
          onChange={e => setCauseFilter(e.target.value as ExceptionCause | '')}
          className="text-sm border border-gray-300 rounded-md px-2 py-1.5 text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">All causes</option>
          {(Object.keys(CAUSE_LABELS) as ExceptionCause[]).map(c => (
            <option key={c} value={c}>{CAUSE_LABELS[c]}</option>
          ))}
        </select>

        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value as ExceptionStatus | '')}
          className="text-sm border border-gray-300 rounded-md px-2 py-1.5 text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">All statuses</option>
          <option value="open">Open</option>
          <option value="approved">Approved</option>
          <option value="edited">Edited</option>
          <option value="rejected">Rejected</option>
        </select>

        {(causeFilter || statusFilter) && (
          <button
            onClick={() => { setCauseFilter(''); setStatusFilter('') }}
            className="text-xs text-gray-500 underline hover:text-gray-700"
          >
            Clear filters
          </button>
        )}

        <span className="ml-auto text-xs text-gray-400">
          {filtered.length} of {exceptions.length} exceptions
        </span>
      </div>

      {/* Table */}
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">ID</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">Cause</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">Side</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">Explanation</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">Confidence</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">Status</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-100">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-400">
                  No exceptions match the current filters.
                </td>
              </tr>
            ) : (
              filtered.map(ex => (
                <tr key={ex.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-xs text-gray-500 whitespace-nowrap">
                    <Link
                      to={`/exceptions/${ex.id}`}
                      className="hover:text-blue-600 hover:underline"
                    >
                      {ex.id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <Link to={`/exceptions/${ex.id}`} className="block">
                      <CauseBadge cause={ex.cause} />
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-gray-700 capitalize whitespace-nowrap">
                    <Link to={`/exceptions/${ex.id}`} className="block hover:text-blue-600">{ex.side}</Link>
                  </td>
                  <td className="px-4 py-3 text-gray-600 max-w-xs truncate">
                    <Link to={`/exceptions/${ex.id}`} className="block hover:text-blue-600 truncate">{ex.explanation}</Link>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-700 whitespace-nowrap">
                    <Link to={`/exceptions/${ex.id}`} className="block hover:text-blue-600">
                      {(ex.llm_confidence * 100).toFixed(0)}%
                    </Link>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <Link to={`/exceptions/${ex.id}`} className="block">
                      <StatusBadge status={ex.status} />
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {autoMatchedCount > 0 && (
        <p className="mt-4 text-xs text-gray-400 text-right">
          {autoMatchedCount} exceptions already approved
        </p>
      )}
    </div>
  )
}
