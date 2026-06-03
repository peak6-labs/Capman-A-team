import { useEffect, useState } from 'react'
import { getControlStatus, setKillSwitch, setDryRun, type ControlStatus } from '../api'

export default function Control() {
  const [status, setStatus] = useState<ControlStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const refresh = () => {
    setLoading(true)
    getControlStatus()
      .then(s => { setStatus(s); setError(null); setLastUpdated(new Date()) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30_000)
    return () => clearInterval(id)
  }, [])

  const toggleKill = async () => {
    if (!status) return
    setBusy(true)
    try {
      const res = await setKillSwitch(!status.kill_switch_engaged)
      setStatus(s => s ? { ...s, kill_switch_engaged: res.kill_switch_engaged } : s)
    } catch (e) { setError(String(e)) }
    setBusy(false)
  }

  const toggleDryRun = async () => {
    if (!status) return
    setBusy(true)
    try {
      const res = await setDryRun(!status.dry_run)
      setStatus(s => s ? { ...s, dry_run: res.dry_run } : s)
    } catch (e) { setError(String(e)) }
    setBusy(false)
  }

  return (
    <div className="page">
      <h1>Control</h1>

      {error && <p className="error">{error}</p>}

      {loading && !status ? (
        <span className="spinner" />
      ) : status && (
        <>
          <div className="grid-2" style={{ marginBottom: '1rem' }}>
            {/* Kill Switch card */}
            <div className="card">
              <h2>Kill Switch</h2>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
                <span className={`badge ${status.kill_switch_engaged ? 'badge-red' : 'badge-green'}`}>
                  {status.kill_switch_engaged ? 'ENGAGED' : 'CLEAR'}
                </span>
                <span className="muted">
                  {status.kill_switch_engaged
                    ? 'All new entries are halted. Live trading is blocked.'
                    : 'Kill switch is clear. Trading gates are active.'}
                </span>
              </div>
              <button
                className={`btn ${status.kill_switch_engaged ? 'btn-green' : 'btn-red'}`}
                onClick={toggleKill}
                disabled={busy}
              >
                {status.kill_switch_engaged ? 'Clear Kill Switch' : 'Engage Kill Switch'}
              </button>
            </div>

            {/* Dry Run card */}
            <div className="card">
              <h2>Dry Run Mode</h2>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
                <span className={`badge ${status.dry_run ? 'badge-yellow' : 'badge-red'}`}>
                  {status.dry_run ? 'DRY RUN ON' : 'LIVE MODE'}
                </span>
                <span className="muted">
                  {status.dry_run
                    ? 'Orders are simulated. Nothing is submitted to Kalshi.'
                    : 'Orders will be submitted live to Kalshi.'}
                </span>
              </div>
              <button
                className={`btn ${status.dry_run ? 'btn-red' : 'btn-gray'}`}
                onClick={toggleDryRun}
                disabled={busy}
              >
                {status.dry_run ? 'Disable Dry Run (go live)' : 'Enable Dry Run'}
              </button>
            </div>
          </div>

          {/* Auth + Exchange */}
          <div className="grid-2">
            <div className="card">
              <h2>Authentication</h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <span className="muted">Credentials:</span>
                  <span className={`badge ${status.auth.credentials_present ? 'badge-green' : 'badge-red'}`}>
                    {status.auth.credentials_present ? 'Present' : 'Missing'}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <span className="muted">Auth OK:</span>
                  <span className={`badge ${status.auth.auth_ok ? 'badge-green' : 'badge-red'}`}>
                    {status.auth.auth_ok ? 'Yes' : 'No'}
                  </span>
                </div>
                {status.auth.error && (
                  <div className="error" style={{ marginTop: '0.5rem' }}>{status.auth.error}</div>
                )}
              </div>
            </div>

            <div className="card">
              <h2>Exchange Status</h2>
              <pre style={{ fontSize: '0.75rem', color: '#94a3b8', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {JSON.stringify(status.exchange, null, 2)}
              </pre>
            </div>
          </div>

          <div style={{ marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <button className="btn btn-gray" onClick={refresh} disabled={loading || busy}>
              {loading ? <span className="spinner" /> : null} Refresh
            </button>
            {lastUpdated && (
              <span className="muted">Updated {lastUpdated.toLocaleTimeString()} · auto-refreshes every 30s</span>
            )}
          </div>
        </>
      )}
    </div>
  )
}
