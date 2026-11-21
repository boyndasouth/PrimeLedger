import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

export default function Upload() {
  const navigate = useNavigate()
  const bankRef = useRef<HTMLInputElement>(null)
  const glRef = useRef<HTMLInputElement>(null)

  const [periodStart, setPeriodStart] = useState('')
  const [periodEnd, setPeriodEnd] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleRun(e: React.FormEvent) {
    e.preventDefault()
    const bankFile = bankRef.current?.files?.[0]
    const glFile = glRef.current?.files?.[0]
    if (!bankFile || !glFile || !periodStart || !periodEnd) {
      setError('All fields are required.')
      return
    }
    setError(null)
    setLoading(true)
    try {
      const run = await api.createRun(bankFile, glFile, periodStart, periodEnd)
      sessionStorage.setItem('primeledger_run_id', run.id)
      navigate(`/queue/${run.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  async function handleDemo() {
    setError(null)
    setLoading(true)
    try {
      const run = await api.seedDemo()
      sessionStorage.setItem('primeledger_run_id', run.run_id)
      navigate(`/queue/${run.run_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-xl mx-auto mt-12 px-4">
      <h1 className="text-2xl font-semibold text-gray-900 mb-1">New reconciliation</h1>
      <p className="text-sm text-gray-500 mb-8">
        Upload a bank statement CSV and a GL export CSV for the period you want to reconcile.
      </p>

      <form onSubmit={handleRun} className="space-y-6">
        {/* Bank statement */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Bank statement CSV
          </label>
          <input
            ref={bankRef}
            type="file"
            accept=".csv"
            className="block w-full text-sm text-gray-700 border border-gray-300 rounded-md px-3 py-2 file:mr-4 file:py-1 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-gray-100 file:text-gray-700 hover:file:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-gray-400">
            Required columns: txn_id, date, description, amount, currency
          </p>
        </div>

        {/* GL export */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            General ledger export CSV
          </label>
          <input
            ref={glRef}
            type="file"
            accept=".csv"
            className="block w-full text-sm text-gray-700 border border-gray-300 rounded-md px-3 py-2 file:mr-4 file:py-1 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-gray-100 file:text-gray-700 hover:file:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-gray-400">
            Required columns: entry_id, date, account, payee, memo, amount, currency
          </p>
        </div>

        {/* Period dates */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Period start</label>
            <input
              type="date"
              value={periodStart}
              onChange={e => setPeriodStart(e.target.value)}
              className="block w-full border border-gray-300 rounded-md px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Period end</label>
            <input
              type="date"
              value={periodEnd}
              onChange={e => setPeriodEnd(e.target.value)}
              className="block w-full border border-gray-300 rounded-md px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
            {error}
          </p>
        )}

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center px-5 py-2.5 text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {loading ? (
              <>
                <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                </svg>
                Running…
              </>
            ) : (
              'Run reconciliation'
            )}
          </button>

          <button
            type="button"
            onClick={handleDemo}
            disabled={loading}
            className="inline-flex items-center px-4 py-2.5 text-sm font-medium rounded-md text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            Load demo data
          </button>
        </div>
      </form>
    </div>
  )
}
