import { useEffect, useState } from 'react'
import { getCurrentTrades, getTradeHistory, type CurrentTrades, type TradeHistory } from '../api'

function ts(v: number | null | undefined) {
  if (!v) return '—'
  return new Date(v).toLocaleString()
}

function pct(v: number | null | undefined) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function outcomeClass(outcome: string) {
  if (outcome === 'placed') return 'pos'
  if (outcome === 'rejected') return 'neg'
  if (outcome === 'dry_run') return ''
  return 'muted'
}

export default function Trades() {
  const [tab, setTab] = useState<'current' | 'history'>('current')
  const [current, setCurrent] = useState<CurrentTrades | null>(null)
  const [history, setHistory] = useState<TradeHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [historyTab, setHistoryTab] = useState<'fills' | 'decisions'>('fills')

  const loadCurrent = () => {
    setLoading(true)
    getCurrentTrades()
      .then(d => { setCurrent(d); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  const loadHistory = () => {
    setLoading(true)
    getTradeHistory()
      .then(d => { setHistory(d); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (tab === 'current') loadCurrent()
    else loadHistory()
  }, [tab])

  return (
    <div className="page">
      <h1>Trades</h1>
      {error && <p className="error">{error}</p>}

      <div className="tabs">
        <button className={`tab ${tab === 'current' ? 'active' : ''}`} onClick={() => setTab('current')}>Current</button>
        <button className={`tab ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>History</button>
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
                  <thead><tr>
                    <th>Ticker</th><th>Position</th><th>Exposure</th><th>Cost</th><th>Realized PnL</th>
                  </tr></thead>
                  <tbody>
                    {current.open_positions.map((p, i) => (
                      <tr key={i}>
                        <td><code style={{ fontSize: '0.75rem', background: '#2d3148', padding: '2px 6px', borderRadius: 4 }}>{p.ticker}</code></td>
                        <td>{p.position ?? '—'}</td>
                        <td>{p.exposure_usd ? `$${parseFloat(p.exposure_usd).toFixed(2)}` : '—'}</td>
                        <td>{p.cost_usd ? `$${parseFloat(p.cost_usd).toFixed(2)}` : '—'}</td>
                        <td>{p.realized_pnl_usd ? `$${parseFloat(p.realized_pnl_usd).toFixed(2)}` : '—'}</td>
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
                  <thead><tr>
                    <th>Ticker</th><th>Action</th><th>Side</th><th>Price</th><th>Count</th><th>Status</th>
                  </tr></thead>
                  <tbody>
                    {current.resting_orders.map((o, i) => (
                      <tr key={i}>
                        <td><code style={{ fontSize: '0.75rem', background: '#2d3148', padding: '2px 6px', borderRadius: 4 }}>{o.ticker}</code></td>
                        <td>{o.action}</td>
                        <td>{o.side}</td>
                        <td>{o.price_usd ? `$${parseFloat(o.price_usd).toFixed(2)}` : '—'}</td>
                        <td>{o.count ?? '—'}</td>
                        <td>{o.status ?? '—'}</td>
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
          <div className="tabs">
            <button className={`tab ${historyTab === 'fills' ? 'active' : ''}`} onClick={() => setHistoryTab('fills')}>
              Fills ({history.fills.length})
            </button>
            <button className={`tab ${historyTab === 'decisions' ? 'active' : ''}`} onClick={() => setHistoryTab('decisions')}>
              Decisions ({history.decisions.length})
            </button>
          </div>

          {historyTab === 'fills' && (
            <div className="card">
              {history.fills.length === 0
                ? <p className="muted">No fills on record.</p>
                : (
                  <table>
                    <thead><tr>
                      <th>Time</th><th>Ticker</th><th>Side</th><th>Action</th><th>Count</th><th>Price</th>
                    </tr></thead>
                    <tbody>
                      {history.fills.map((f, i) => (
                        <tr key={i}>
                          <td className="muted">{ts(f.ts)}</td>
                          <td><code style={{ fontSize: '0.75rem', background: '#2d3148', padding: '2px 6px', borderRadius: 4 }}>{f.ticker ?? '—'}</code></td>
                          <td>{f.side ?? '—'}</td>
                          <td>{f.action ?? '—'}</td>
                          <td>{f.count ?? '—'}</td>
                          <td>{f.price_usd ? `$${parseFloat(f.price_usd).toFixed(2)}` : '—'}</td>
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
                    <thead><tr>
                      <th>Time</th><th>Ticker</th><th>Side</th><th>Outcome</th><th>Confidence</th><th>Gate</th><th>Reason</th>
                    </tr></thead>
                    <tbody>
                      {history.decisions.map((d, i) => (
                        <tr key={i}>
                          <td className="muted">{ts(d.ts)}</td>
                          <td><code style={{ fontSize: '0.75rem', background: '#2d3148', padding: '2px 6px', borderRadius: 4 }}>{d.market_ticker ?? '—'}</code></td>
                          <td>{d.side ?? '—'}</td>
                          <td className={outcomeClass(d.outcome)}>{d.outcome}</td>
                          <td>{pct(d.confidence)}</td>
                          <td className="muted">{d.gate ?? '—'}</td>
                          <td className="muted" style={{ maxWidth: 250, wordBreak: 'break-word' }}>{d.reason ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
            </div>
          )}
        </>
      )}

      <button className="btn btn-gray" onClick={() => tab === 'current' ? loadCurrent() : loadHistory()} disabled={loading} style={{ marginTop: '0.5rem' }}>
        {loading ? <span className="spinner" /> : null} Refresh
      </button>
    </div>
  )
}
