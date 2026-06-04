import { useEffect, useState } from 'react'
import { getCurrentTrades, getTradeHistory, type CurrentTrades, type TradeHistory } from '../api'
import MarketCell from '../components/MarketCell'
import { ActionBadge, PositionSideBadge, SideBadge } from '../components/TradeBadges'

function ts(v: number | null | undefined) {
  if (!v) return '—'
  return new Date(v).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function pct(v: number | null | undefined) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const cls =
    outcome === 'placed' ? 'badge-green' :
    outcome === 'rejected' ? 'badge-red' :
    outcome === 'dry_run' ? 'badge-orange' :
    'badge-gray'
  return <span className={`badge ${cls}`}>{outcome}</span>
}

export default function Trades() {
  const [tab, setTab] = useState<'current' | 'history'>('current')
  const [current, setCurrent] = useState<CurrentTrades | null>(null)
  const [history, setHistory] = useState<TradeHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [historyTab, setHistoryTab] = useState<'fills' | 'decisions'>('fills')
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const loadCurrent = () => {
    setLoading(true)
    getCurrentTrades()
      .then(d => { setCurrent(d); setError(null); setLastUpdated(new Date()) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  const loadHistory = () => {
    setLoading(true)
    getTradeHistory()
      .then(d => { setHistory(d); setError(null); setLastUpdated(new Date()) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (tab === 'current') {
      queueMicrotask(loadCurrent)
      const id = setInterval(loadCurrent, 30_000)
      return () => clearInterval(id)
    } else {
      queueMicrotask(loadHistory)
    }
  }, [tab])

  return (
    <div className="page">
      <h1>Trades</h1>
      {error && <p className="error">{error}</p>}

      <div className="tabs-line">
        <button className={`tab-line ${tab === 'current' ? 'active' : ''}`} onClick={() => setTab('current')}>
          Current
        </button>
        <button className={`tab-line ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>
          History
        </button>
      </div>

      {loading && <span className="spinner" />}

      {tab === 'current' && current && !loading && (
        <>
          <div className="card">
            <h2>Open Positions ({current.open_positions.length})</h2>
            {current.open_positions.length === 0
              ? <p className="muted">No open positions.</p>
              : (
                <table>
                  <thead>
                    <tr>
                      <th>Market</th><th>Position</th><th>Exposure</th><th>Cost</th><th>Realized PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.open_positions.map((p, i) => (
                      <tr key={i}>
                        <td><MarketCell ticker={p.ticker} /></td>
                        <td><PositionSideBadge position={p.position} /></td>
                        <td>{p.exposure_usd ? `$${parseFloat(p.exposure_usd).toFixed(2)}` : '—'}</td>
                        <td>{p.cost_usd ? `$${parseFloat(p.cost_usd).toFixed(2)}` : '—'}</td>
                        <td className={p.realized_pnl_usd && parseFloat(p.realized_pnl_usd) >= 0 ? 'pos' : 'neg'}>
                          {p.realized_pnl_usd ? `$${parseFloat(p.realized_pnl_usd).toFixed(2)}` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </div>

          <div className="card">
            <h2>Resting Orders ({current.resting_orders.length})</h2>
            {current.resting_orders.length === 0
              ? <p className="muted">No resting orders.</p>
              : (
                <table>
                  <thead>
                    <tr>
                      <th>Market</th><th>Action</th><th>Side</th><th>Price</th><th>Count</th><th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.resting_orders.map((o, i) => (
                      <tr key={i}>
                        <td><MarketCell ticker={o.ticker} /></td>
                        <td><ActionBadge action={o.action} /></td>
                        <td><SideBadge side={o.side} /></td>
                        <td>{o.price_usd ? `$${parseFloat(o.price_usd).toFixed(2)}` : '—'}</td>
                        <td>{o.count ?? '—'}</td>
                        <td><span className="badge badge-gray">{o.status ?? '—'}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </div>
        </>
      )}

      {tab === 'history' && history && !loading && (
        <>
          <div className="tabs-line" style={{ marginBottom: '1rem' }}>
            <button
              className={`tab-line ${historyTab === 'fills' ? 'active' : ''}`}
              onClick={() => setHistoryTab('fills')}
            >
              Fills ({history.fills.length})
            </button>
            <button
              className={`tab-line ${historyTab === 'decisions' ? 'active' : ''}`}
              onClick={() => setHistoryTab('decisions')}
            >
              Decisions ({history.decisions.length})
            </button>
          </div>

          {historyTab === 'fills' && (
            <div className="card">
              {history.fills.length === 0
                ? <p className="muted">No fills on record.</p>
                : (
                  <table>
                    <thead>
                      <tr>
                        <th>Time</th><th>Market</th><th>Side</th><th>Action</th><th>Count</th><th>Price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.fills.map((f, i) => (
                        <tr key={i}>
                          <td className="muted" style={{ whiteSpace: 'nowrap' }}>{ts(f.ts)}</td>
                          <td><MarketCell ticker={f.ticker} /></td>
                          <td><SideBadge side={f.side ?? null} /></td>
                          <td><ActionBadge action={f.action ?? null} /></td>
                          <td>{f.count ?? '—'}</td>
                          <td>{f.price_usd ? `${(parseFloat(f.price_usd) * 100).toFixed(1)}¢` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
            </div>
          )}

          {historyTab === 'decisions' && (
            <div className="card">
              {history.decisions.length === 0
                ? <p className="muted">No decisions in journal.</p>
                : (
                  <table>
                    <thead>
                      <tr>
                        <th>Time</th><th>Market</th><th>Side</th><th>Outcome</th>
                        <th>Confidence</th><th>Gate</th><th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.decisions.map((d, i) => (
                        <tr key={i}>
                          <td className="muted" style={{ whiteSpace: 'nowrap' }}>{ts(d.ts)}</td>
                          <td><MarketCell ticker={d.market_ticker} /></td>
                          <td><SideBadge side={d.side ?? null} /></td>
                          <td><OutcomeBadge outcome={d.outcome} /></td>
                          <td>{pct(d.confidence)}</td>
                          <td className="muted">{d.gate ?? '—'}</td>
                          <td className="muted" style={{ maxWidth: 240, wordBreak: 'break-word' }}>{d.reason ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
            </div>
          )}
        </>
      )}

      <div style={{ marginTop: '1.25rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
        <button
          className="btn btn-gray"
          onClick={() => tab === 'current' ? loadCurrent() : loadHistory()}
          disabled={loading}
        >
          {loading ? <span className="spinner" /> : null} Refresh
        </button>
        {lastUpdated && (
          <span className="muted">
            Updated {lastUpdated.toLocaleTimeString()}{tab === 'current' ? ' · auto-refreshes every 30s' : ''}
          </span>
        )}
      </div>
    </div>
  )
}
