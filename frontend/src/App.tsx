import { useEffect, useState } from 'react'
import { NavLink, Route, BrowserRouter as Router, Routes } from 'react-router-dom'
import { registerWakeupSetter } from './api'
import Audit from './pages/Audit'
import ExceptionDetail from './pages/ExceptionDetail'
import Metrics from './pages/Metrics'
import Queue from './pages/Queue'
import Upload from './pages/Upload'

const NAV_LINKS = [
  { to: '/',        label: 'Upload' },
  { to: '/queue',   label: 'Queue' },
  { to: '/audit',   label: 'Audit' },
  { to: '/metrics', label: 'Metrics' },
]

function NavBar({ waking }: { waking: boolean }) {
  return (
    <header className="border-b border-gray-200 bg-white sticky top-0 z-10">
      <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-6">
        <span className="font-semibold text-gray-900 text-sm tracking-tight select-none">
          PrimeLedger
        </span>
        <nav className="flex items-center gap-1">
          {NAV_LINKS.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-gray-100 text-gray-900'
                    : 'text-gray-500 hover:text-gray-900 hover:bg-gray-50'
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>

        {waking && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2.5 py-1">
            <svg className="animate-spin h-3 w-3" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
            Backend waking up…
          </span>
        )}
      </div>
    </header>
  )
}

export default function App() {
  const [waking, setWaking] = useState(false)

  useEffect(() => {
    registerWakeupSetter(setWaking)
  }, [])

  return (
    <Router>
      <div className="min-h-screen bg-gray-50">
        <NavBar waking={waking} />
        <main>
          <Routes>
            <Route path="/" element={<Upload />} />
            <Route path="/queue/:runId" element={<Queue />} />
            <Route path="/queue" element={<Queue />} />
            <Route path="/exceptions/:id" element={<ExceptionDetail />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/metrics" element={<Metrics />} />
          </Routes>
        </main>
      </div>
    </Router>
  )
}
