import { useEffect, useState } from 'react'
import { api, type Metrics } from '../api'

// ── helpers ───────────────────────────────────────────────────────────────

function pct(val: number | null | undefined): string {
  if (val == null) return '—'
  return `${(val * 100).toFixed(1)}%`
}

function secs(val: number | null | undefined): string {
  if (val == null) return '—'
  return `${val.toFixed(2)}s`
}

// ── stat card ─────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  sub,
  highlight,
}: {
  label: string
  value: string
  sub?: string
  highlight?: 'green' | 'red' | 'neutral'
}) {
  const accent =
    highlight === 'green' ? 'border-green-200 bg-green-50' :
    highlight === 'red'   ? 'border-red-200 bg-red-50' :
    'border-gray-200 bg-white'

  return (
    <div className={`border rounded-lg px-5 py-4 ${accent}`}>
      <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-gray-900 tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-gray-400">{sub}</p>}
    </div>
  )
}

// ── pipeline stage bar ────────────────────────────────────────────────────

function StageBar({ label, seconds, maxSeconds }: { label: string; seconds: number | null | undefined; maxSeconds: number }) {
  const pctWidth = maxSeconds > 0 && seconds != null ? Math.max(2, (seconds / maxSeconds) * 100) : 0
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-gray-500 w-28 shrink-0">{label}</span>
      <div className="flex-1 bg-gray-100 rounded-full h-2 overflow-hidden">
        <div className="bg-blue-500 h-2 rounded-full" style={{ width: `${pctWidth}%` }} />
      </div>
      <span className="text-xs tabular-nums text-gray-700 w-14 text-right">{secs(seconds)}</span>
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────

export default function Metrics() {
  const [runIdInput, setRunIdInput] = useState('')
  const [metrics, setMetrics]       = useState<Metrics | null>(null)
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState<string | null>(null)

  // If a run_id is stored from a previous session, pre-populate
  useEffect(() => {
    const stored = sessionStorage.getItem('primeledger_run_id')
    if (stored) setRunIdInput(stored)
  }, [])

  function loadMetrics() {
    const rid = runIdInput.trim()
    if (!rid) return
    setLoading(true)
    setError(null)
    api.getMetrics(rid)
      .then(m => { setMetrics(m); sessionStorage.setItem('primeledger_run_id', rid) })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }

  // Compute time comparison numbers
  const manualMinutes = metrics?.manual_baseline_minutes ?? 2
  // For comparison we need total records — the metrics endpoint doesn't return them directly.
  // We show pipeline_seconds.total vs (manualMinutes × some record count) where count is unavailable,
  // so we display the per-record baseline assumption and the actual pipeline time.
  const pipelineTotal = metrics?.pipeline_seconds?.total ?? null

  const maxStageSeconds = metrics
    ? Math.max(
        metrics.pipeline_seconds?.ingestion ?? 0,
        metrics.pipeline_seconds?.matching ?? 0,
        metrics.pipeline_seconds?.investigation ?? 0,
        metrics.pipeline_seconds?.resolution ?? 0,
      )
    : 0

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">Metrics</h1>
        <p className="text-sm text-gray-500 mt-1">Accuracy, throughput, and time savings for the reconciliation run.</p>
      </div>

      {/* Run ID loader */}
      <div className="flex items-center gap-2 mb-6">
        <input
          type="text"
          value={runIdInput}
          onChange={e => setRunIdInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && loadMetrics()}
          placeholder="Paste a Run ID…"
          className="flex-1 max-w-sm border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={loadMetrics}
          disabled={loading || !runIdInput.trim()}
          className="px-3 py-1.5 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Load
        </button>
      </div>

      {error && (
        <p className="mb-5 text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>
      )}

      {loading && (
        <div className="flex items-center justify-center h-40 text-sm text-gray-400">Loading…</div>
      )}

      {!loading && !metrics && (
        <div className="border border-dashed border-gray-200 rounded-lg flex items-center justify-center h-40">
          <p className="text-sm text-gray-400">Enter a Run ID above to view metrics.</p>
        </div>
      )}

      {metrics && (
        <>
          {/* ── Accuracy cards ── */}
          <section className="mb-8">
            <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">Accuracy</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatCard
                label="Auto-match rate"
                value={pct(metrics.auto_match_rate)}
                sub="auto-accepted / total"
                highlight={metrics.auto_match_rate >= 0.7 ? 'green' : 'neutral'}
              />
              <StatCard
                label="Match precision"
                value={pct(metrics.match_precision)}
                sub="vs labeled set"
                highlight={
                  metrics.match_precision == null ? 'neutral' :
                  metrics.match_precision >= 0.9 ? 'green' : 'red'
                }
              />
              <StatCard
                label="Match recall"
                value={pct(metrics.match_recall)}
                sub="vs labeled set"
                highlight={
                  metrics.match_recall == null ? 'neutral' :
                  metrics.match_recall >= 0.9 ? 'green' : 'red'
                }
              />
              <StatCard
                label="Cause accuracy"
                value={pct(metrics.cause_accuracy)}
                sub="exception causes"
                highlight={
                  metrics.cause_accuracy == null ? 'neutral' :
                  metrics.cause_accuracy >= 0.8 ? 'green' : 'red'
                }
              />
            </div>
          </section>

          {/* ── Audit completeness ── */}
          <section className="mb-8">
            <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">Audit</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatCard
                label="Audit complete"
                value={metrics.audit_complete ? 'Yes' : 'No'}
                sub="every record has ≥ 1 log row"
                highlight={metrics.audit_complete ? 'green' : 'red'}
              />
            </div>
          </section>

          {/* ── Pipeline timing ── */}
          <section className="mb-8">
            <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">Pipeline timing</h2>
            <div className="bg-white border border-gray-200 rounded-lg px-5 py-4 space-y-3">
              <StageBar label="Ingestion"     seconds={metrics.pipeline_seconds?.ingestion}     maxSeconds={maxStageSeconds} />
              <StageBar label="Matching"      seconds={metrics.pipeline_seconds?.matching}      maxSeconds={maxStageSeconds} />
              <StageBar label="Investigation" seconds={metrics.pipeline_seconds?.investigation} maxSeconds={maxStageSeconds} />
              <StageBar label="Resolution"    seconds={metrics.pipeline_seconds?.resolution}    maxSeconds={maxStageSeconds} />
              <div className="border-t border-gray-100 pt-3">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium text-gray-700">Total pipeline time</span>
                  <span className="tabular-nums font-semibold text-gray-900">{secs(pipelineTotal)}</span>
                </div>
              </div>
            </div>
          </section>

          {/* ── Before / after time comparison ── */}
          <section className="mb-6">
            <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">Time savings estimate</h2>
            <div className="bg-white border border-gray-200 rounded-lg px-5 py-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                {/* Manual baseline */}
                <div>
                  <p className="text-xs text-gray-500 uppercase tracking-wide font-medium mb-1">Manual baseline</p>
                  <p className="text-3xl font-semibold text-gray-800 tabular-nums">
                    {manualMinutes} min
                    <span className="text-sm font-normal text-gray-400"> / record</span>
                  </p>
                  <p className="mt-2 text-xs text-gray-400 leading-relaxed">
                    Assumption: an accountant takes approximately <strong>{manualMinutes} minutes</strong> per
                    transaction to manually match and document each reconciliation item — including
                    lookup in the GL, comparison against the bank statement, and writing the journal
                    entry note. This is a conservative industry estimate for a mid-size company without
                    automation tooling.
                  </p>
                </div>

                {/* PrimeLedger */}
                <div>
                  <p className="text-xs text-gray-500 uppercase tracking-wide font-medium mb-1">PrimeLedger pipeline</p>
                  <p className="text-3xl font-semibold text-green-700 tabular-nums">
                    {pipelineTotal != null ? `${pipelineTotal.toFixed(1)}s` : '—'}
                    <span className="text-sm font-normal text-gray-400"> total</span>
                  </p>
                  <p className="mt-2 text-xs text-gray-400 leading-relaxed">
                    Four agents (ingestion → matching → investigation → resolution) run sequentially.
                    Only the exception queue requires human review — typically a fraction of the full
                    record set. The auto-match rate ({pct(metrics.auto_match_rate)}) measures how many
                    records were resolved without manual intervention.
                  </p>
                </div>
              </div>

              {/* Visualisation bar */}
              {pipelineTotal != null && (
                <div className="mt-5 border-t border-gray-100 pt-4">
                  <p className="text-xs text-gray-500 mb-2">
                    Relative comparison — pipeline seconds vs manual minutes (single record)
                  </p>
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <span className="text-xs text-gray-500 w-32 shrink-0">Manual (1 record)</span>
                      <div className="flex-1 bg-red-100 rounded-full h-3">
                        <div className="bg-red-400 h-3 rounded-full w-full" />
                      </div>
                      <span className="text-xs tabular-nums text-gray-700 w-16 text-right">{manualMinutes * 60}s</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-xs text-gray-500 w-32 shrink-0">PrimeLedger (all)</span>
                      <div className="flex-1 bg-gray-100 rounded-full h-3 overflow-hidden">
                        <div
                          className="bg-green-500 h-3 rounded-full"
                          style={{ width: `${Math.min(100, (pipelineTotal / (manualMinutes * 60)) * 100)}%` }}
                        />
                      </div>
                      <span className="text-xs tabular-nums text-gray-700 w-16 text-right">{pipelineTotal.toFixed(1)}s</span>
                    </div>
                  </div>
                  <p className="mt-2 text-xs text-gray-400">
                    Note: manual baseline is per-record; PrimeLedger processes the entire data set in one pipeline run.
                  </p>
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
