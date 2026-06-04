import { useEffect, useRef, useState } from 'react'
import { getControlStatus, setKillSwitch, setDryRun, getSignals, getCurrentTrades, type ControlStatus, type SignalsResponse, type CurrentTrades } from '../api'
import AgentChat from '../components/AgentChat'
import MarketCell from '../components/MarketCell'
import { PositionSideBadge, SideBadge } from '../components/TradeBadges'

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
      background: ok ? 'var(--pos)' : 'var(--neg)',
      boxShadow: ok ? '0 0 6px var(--pos)' : '0 0 6px var(--neg)',
      flexShrink: 0,
    }} />
  )
}

export default function Control() {
  const [status, setStatus] = useState<ControlStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const [signals, setSignals] = useState<SignalsResponse | null>(null)
  const [signalsLoading, setSignalsLoading] = useState(false)
  const [signalsError, setSignalsError] = useState<string | null>(null)
  const [signalsCoolingDown, setSignalsCoolingDown] = useState(false)
  const signalsPollRef = useRef<number | null>(null)
  const signalsCooldownRef = useRef<number | null>(null)

  const [positions, setPositions] = useState<CurrentTrades | null>(null)
  const [positionsError, setPositionsError] = useState<string | null>(null)

  const refresh = () => {
    setLoading(true)
    getControlStatus()
      .then(s => { setStatus(s); setError(null); setLastUpdated(new Date()) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  const clearSignalsPoll = () => {
    if (signalsPollRef.current !== null) {
      window.clearTimeout(signalsPollRef.current)
      signalsPollRef.current = null
    }
  }

  const clearSignalsCooldown = () => {
    if (signalsCooldownRef.current !== null) {
      window.clearTimeout(signalsCooldownRef.current)
      signalsCooldownRef.current = null
    }
  }

  const loadSignals = (isPoll = false) => {
    clearSignalsPoll()
    clearSignalsCooldown()
    if (!isPoll) setSignalsLoading(true)
    getSignals(10, !isPoll)
      .then(s => {
        setSignals(s)
        const retryAt = s.scan_next_retry_at ? new Date(s.scan_next_retry_at) : null
        const retryMs = retryAt ? retryAt.getTime() - Date.now() : 0
        const retryText = retryAt && retryMs > 0
          ? ` Retry after ${retryAt.toLocaleTimeString()}.`
          : ''
        setSignalsError(s.scan_error ? `${s.scan_error}${retryText}` : null)
        setSignalsCoolingDown(retryMs > 0)
        if (retryMs > 0) {
          signalsCooldownRef.current = window.setTimeout(() => setSignalsCoolingDown(false), retryMs)
        }
        if (s.scan_status === 'running') {
          setSignalsLoading(true)
          signalsPollRef.current = window.setTimeout(() => loadSignals(true), 3_000)
        } else {
          setSignalsLoading(false)
        }
      })
      .catch(e => {
        setSignalsError(String(e))
        setSignalsLoading(false)
      })
  }

  const loadPositions = () => {
    getCurrentTrades()
      .then(d => { setPositions(d); setPositionsError(null) })
      .catch(e => setPositionsError(String(e)))
  }

  useEffect(() => {
    queueMicrotask(() => { refresh(); loadPositions() })
    const id = setInterval(() => { refresh(); loadPositions() }, 30_000)
    return () => {
      clearInterval(id)
      clearSignalsPoll()
      clearSignalsCooldown()
    }
  }, [])

  const updateStatus = async (fn: () => Promise<Partial<ControlStatus>>) => {
    if (!status) return
    setBusy(true)
    try {
      const patch = await fn()
      setStatus(s => s ? { ...s, ...patch } : s)
    } catch (e) { setError(String(e)) }
    setBusy(false)
  }

  const toggleKill = () => updateStatus(() => setKillSwitch(!status!.kill_switch_engaged))

  const toggleDryRun = () => updateStatus(() => setDryRun(!status!.dry_run))

  const exchangeActive = status?.exchange?.exchange_active === true || status?.exchange?.trading_active === true

  return (
    <div className="page">

      {/* ── Page header ── */}
      <div className="home-header">
        <div>
          <h1 style={{ marginBottom: '0.2rem' }}>Home</h1>
          {lastUpdated && (
            <span className="muted">Updated {lastUpdated.toLocaleTimeString()} · auto-refreshes every 30s</span>
          )}
        </div>
        <button className="btn btn-gray" onClick={refresh} disabled={loading || busy} style={{ height: 34, fontSize: '0.8rem' }}>
          {loading ? <span className="spinner" /> : '↻'} Refresh
        </button>
      </div>

      {error && <p className="error" style={{ marginBottom: '1rem' }}>{error}</p>}

      {/* ── System status strip ── */}
      {status && (
        <div className="home-status-strip">
          <div className="home-status-item">
            <StatusDot ok={!status.kill_switch_engaged} />
            <span className="home-status-label">Kill Switch</span>
            <span className={`home-status-value ${status.kill_switch_engaged ? 'home-status-red' : 'home-status-green'}`}>
              {status.kill_switch_engaged ? 'ENGAGED' : 'CLEAR'}
            </span>
            <button
              className={`btn home-status-btn ${status.kill_switch_engaged ? 'btn-green' : 'btn-red'}`}
              onClick={toggleKill}
              disabled={busy}
            >
              {status.kill_switch_engaged ? 'Clear' : 'Engage'}
            </button>
          </div>

          <div className="home-status-divider" />

          <div className="home-status-item">
            <StatusDot ok={status.dry_run} />
            <span className="home-status-label">Mode</span>
            <span className={`home-status-value ${status.dry_run ? 'home-status-yellow' : 'home-status-red'}`}>
              {status.dry_run ? 'DRY RUN' : 'LIVE'}
            </span>
            <button
              className={`btn home-status-btn ${status.dry_run ? 'btn-red' : 'btn-gray'}`}
              onClick={toggleDryRun}
              disabled={busy}
            >
              {status.dry_run ? 'Go Live' : 'Enable Dry Run'}
            </button>
          </div>

          <div className="home-status-divider" />

          <div className="home-status-item">
            <StatusDot ok={status.auth.auth_ok} />
            <span className="home-status-label">Auth</span>
            <span className={`home-status-value ${status.auth.auth_ok ? 'home-status-green' : 'home-status-red'}`}>
              {status.auth.auth_ok ? 'OK' : 'FAILED'}
            </span>
          </div>

          <div className="home-status-divider" />

          <div className="home-status-item">
            <StatusDot ok={exchangeActive} />
            <span className="home-status-label">Exchange</span>
            <span className={`home-status-value ${exchangeActive ? 'home-status-green' : 'home-status-red'}`}>
              {exchangeActive ? 'ACTIVE' : 'CLOSED'}
            </span>
          </div>

          {positions && (
            <>
              <div className="home-status-divider" />
              <div className="home-status-item">
                <span className="home-status-label">Positions</span>
                <span className="home-status-value home-status-neutral">{positions.open_positions.length}</span>
              </div>
              <div className="home-status-divider" />
              <div className="home-status-item">
                <span className="home-status-label">Resting Orders</span>
                <span className="home-status-value home-status-neutral">{positions.resting_orders.length}</span>
              </div>
            </>
          )}
        </div>
      )}

      <div className="home-main-grid">
        {/* ── Left column ── */}
        <div className="home-left">

          {/* Active Positions */}
          <div className="card" style={{ marginBottom: '1rem' }}>
            <div className="home-card-header">
              <h2 style={{ margin: 0 }}>
                Active Positions
                {positions && (
                  <span className="home-count-chip">{positions.open_positions.length}</span>
                )}
              </h2>
              <button className="btn btn-gray home-card-btn" onClick={loadPositions}>Refresh</button>
            </div>
            {positionsError && <p className="error">{positionsError}</p>}
            {!positions ? (
              <p className="muted">Loading…</p>
            ) : positions.open_positions.length === 0 ? (
              <p className="muted">No open positions.</p>
            ) : (
              <div style={{ overflowY: 'auto', maxHeight: 220 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Market</th><th>Side</th><th>Exposure</th><th>Realized PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.open_positions.map((p, i) => (
                      <tr key={i}>
                        <td><MarketCell ticker={p.ticker} fallbackTitle={p.ticker} /></td>
                        <td><PositionSideBadge position={p.position} /></td>
                        <td>{p.exposure_usd ? `$${parseFloat(p.exposure_usd).toFixed(2)}` : '—'}</td>
                        <td className={p.realized_pnl_usd && parseFloat(p.realized_pnl_usd) >= 0 ? 'pos' : 'neg'}>
                          {p.realized_pnl_usd ? `$${parseFloat(p.realized_pnl_usd).toFixed(2)}` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Live Agent Queue */}
          <div className="card">
            <div className="home-card-header">
              <h2 style={{ margin: 0 }}>
                Live Agent Queue
                {signals && (
                  <span className="home-count-chip">{signals.candidates.length}</span>
                )}
              </h2>
              <button className="btn btn-gray home-card-btn" onClick={() => loadSignals()} disabled={signalsLoading || signalsCoolingDown}>
                {signalsLoading ? <span className="spinner" /> : 'Scan Now'}
              </button>
            </div>

            {signalsError && <p className="error">{signalsError}</p>}

            {!signals && !signalsLoading && !signalsError ? (
              <p className="muted">Click "Scan Now" to screen open markets for candidates.</p>
            ) : (signalsLoading || signals?.scan_status === 'running') && (!signals || signals.candidates.length === 0) ? (
              <p className="muted">Scanning all open markets…</p>
            ) : signals && signals.candidates.length === 0 ? (
              <p className="muted">No compliant liquid candidates found across open markets.</p>
            ) : signals ? (
              <>
                <div style={{ overflowX: 'auto' }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Market</th>
                        <th>Category</th>
                        <th>Side</th>
                        <th>Price</th>
                        <th>Spread</th>
                        <th>Score</th>
                        <th>Expires</th>
                      </tr>
                    </thead>
                    <tbody>
                      {signals.candidates.map((c, i) => (
                        <tr key={i}>
                          <td><MarketCell ticker={c.ticker} fallbackTitle={c.title} /></td>
                          <td><span className="badge badge-gray">{c.category ?? '—'}</span></td>
                          <td><SideBadge side={c.side} uppercase /></td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(parseFloat(c.price) * 100).toFixed(1)}¢</td>
                          <td className="muted" style={{ fontVariantNumeric: 'tabular-nums' }}>{(parseFloat(c.spread) * 100).toFixed(1)}¢</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{c.score.toFixed(3)}</td>
                          <td className="muted">{c.hours_to_expiry}h</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="muted" style={{ marginTop: '0.5rem', fontSize: '0.7rem' }}>
                  Tail signal strength · cached 60s · passes compliance + liquidity + time filters
                </p>
              </>
            ) : null}
          </div>
        </div>

        {/* ── Right column: Agent Chat ── */}
        <div className="home-right">
          <AgentChat username={status?.username} />
        </div>
      </div>
    </div>
  )
}
