import { useState } from 'react'
import { api, type AuditRow, type VerifyResult } from '../api'

function fmtTs(ts: string): string {
  try {
    return new Date(ts).toLocaleString('en-US', {
      year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch {
    return ts
  }
}

// action → readable colour hint
function actionColour(action: string): string {
  if (action.includes('approved')) return 'text-green-700'
  if (action.includes('rejected')) return 'text-red-600'
  if (action.includes('edited'))   return 'text-blue-600'
  if (action.includes('created') || action.includes('completed') || action.includes('drafted')) return 'text-gray-700'
  return 'text-gray-500'
}

export default function Audit() {
  const [rows, setRows]           = useState<AuditRow[]>([])
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null)
  const [verifyError, setVerifyError]   = useState<string | null>(null)

  const [runIdInput, setRunIdInput] = useState(() =>
    sessionStorage.getItem('primeledger_run_id') ?? ''
  )

  function loadRun() {
    const rid = runIdInput.trim()
    if (!rid) return
    setLoading(true)
    setError(null)
    api.getAudit(rid)
      .then(setRows)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }

  async function verifyChain() {
    setVerifying(true)
    setVerifyResult(null)
    setVerifyError(null)
    try {
      const result = await api.verifyChain()
      setVerifyResult(result)
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : 'Verify failed')
    } finally {
      setVerifying(false)
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Audit log</h1>
          <p className="text-sm text-gray-500 mt-1">Chronological record of every agent and user action.</p>
        </div>

        {/* Verify chain button */}
        <button
          onClick={verifyChain}
          disabled={verifying}
          className="px-4 py-2 rounded-md text-sm font-medium bg-gray-900 text-white hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
        >
          {verifying && (
            <svg className="animate-spin h-3.5 w-3.5" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
          )}
          Verify chain
        </button>
      </div>

      {/* Verify result banner */}
      {verifyResult && (
        <div className={`mb-5 flex items-start gap-3 rounded-lg px-4 py-3 border ${
          verifyResult.valid
            ? 'bg-green-50 border-green-200 text-green-800'
            : 'bg-red-50 border-red-200 text-red-800'
        }`}>
          <span className="text-lg leading-none">{verifyResult.valid ? '✓' : '✗'}</span>
          <div>
            <p className="text-sm font-semibold">
              {verifyResult.valid ? 'Hash chain is valid' : 'Hash chain is broken'}
            </p>
            {!verifyResult.valid && verifyResult.broken_at_seq != null && (
              <p className="text-xs mt-0.5">
                First invalid entry at sequence <span className="font-mono font-semibold">{verifyResult.broken_at_seq}</span>
              </p>
            )}
          </div>
        </div>
      )}
      {verifyError && (
        <p className="mb-5 text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{verifyError}</p>
      )}

      {/* Run ID loader */}
      <div className="flex items-center gap-2 mb-5">
        <input
          type="text"
          value={runIdInput}
          onChange={e => setRunIdInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && loadRun()}
          placeholder="Paste a Run ID to load its audit rows…"
          className="flex-1 max-w-sm border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={loadRun}
          disabled={loading || !runIdInput.trim()}
          className="px-3 py-1.5 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Load
        </button>
      </div>

      {error && (
        <p className="mb-4 text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>
      )}

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center h-40 text-sm text-gray-400">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="border border-dashed border-gray-200 rounded-lg flex items-center justify-center h-40">
          <p className="text-sm text-gray-400">Enter a Run ID above to view audit rows, or verify the full chain integrity with the button above.</p>
        </div>
      ) : (
        <>
          <p className="text-xs text-gray-400 mb-2">{rows.length} entries</p>
          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide w-12">#</th>
                  <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide w-44">Timestamp</th>
                  <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">Actor</th>
                  <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">Action</th>
                  <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">Entity</th>
                  <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide w-28">Hash (tail)</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-100">
                {rows.map(row => (
                  <tr key={row.seq} className="hover:bg-gray-50">
                    <td className="px-3 py-2.5 font-mono text-xs text-gray-400">{row.seq}</td>
                    <td className="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap">{fmtTs(row.ts)}</td>
                    <td className="px-3 py-2.5 text-xs text-gray-700 font-mono">{row.actor}</td>
                    <td className={`px-3 py-2.5 text-xs font-medium ${actionColour(row.action)}`}>
                      {row.action}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-gray-500">
                      <span className="font-medium text-gray-700">{row.entity_type}</span>{' '}
                      <span className="font-mono text-gray-400">{row.entity_id.slice(0, 8)}…</span>
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-gray-400 truncate max-w-[7rem]" title={row.hash}>
                      {row.hash.slice(-8)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
