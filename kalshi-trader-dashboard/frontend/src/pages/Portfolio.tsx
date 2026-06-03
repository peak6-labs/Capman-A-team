import { useEffect, useState } from 'react'
import { getPortfolio, type PortfolioResponse } from '../api'

function fmt(v: string | null | undefined) {
  if (v == null) return '—'
  const n = parseFloat(v)
  if (isNaN(n)) return v
  return `$${n.toFixed(2)}`
}

function pnlClass(v: string | null | undefined) {
  if (!v) return ''
  const n = parseFloat(v)
  if (isNaN(n)) return ''
  return n >= 0 ? 'pos' : 'neg'
}

export default function Portfolio() {
  const [data, setData] = useState<PortfolioResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = () => {
    setLoading(true)
    getPortfolio()
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { refresh() }, [])

  return (
    <div className="page">
      <h1>Portfolio</h1>
      {error && <p className="error">{error}</p>}
      {loading && !data ? <span className="spinner" /> : data && (
        <>
          <div className="grid-3" style={{ marginBottom: '1rem' }}>
            <div className="card">
              <div className="stat-label">Cash Balance</div>
              <div className="stat-value">{fmt(data.cash_balance_usd)}</div>
            </div>
            <div className="card">
              <div className="stat-label">Portfolio Value</div>
              <div className="stat-value">{fmt(data.portfolio_value_usd)}</div>
            </div>
            <div className="card">
              <div className="stat-label">Open Positions</div>
              <div className="stat-value">{data.open_positions.length}</div>
            </div>
          </div>

          <div className="card">
            <h2>Open Positions</h2>
            {data.open_positions.length === 0
              ? <p className="muted">No open positions.</p>
              : (
                <table>
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Position</th>
                      <th>Exposure</th>
                      <th>Cost Basis</th>
                      <th>Realized PnL</th>
                      <th>Unrealized PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.open_positions.map((p, i) => (
                      <tr key={i}>
                        <td><code style={{ fontSize: '0.75rem', background: '#2d3148', padding: '2px 6px', borderRadius: 4 }}>{p.ticker}</code></td>
                        <td>{p.position ?? '—'}</td>
                        <td>{fmt(p.exposure_usd)}</td>
                        <td>{fmt(p.cost_usd)}</td>
                        <td className={pnlClass(p.realized_pnl_usd)}>{fmt(p.realized_pnl_usd)}</td>
                        <td className={pnlClass(p.unrealized_pnl_usd)}>{fmt(p.unrealized_pnl_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </div>

          <div className="card">
            <h2>Resting Orders</h2>
            {data.resting_orders.length === 0
              ? <p className="muted">No resting orders.</p>
              : (
                <table>
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Action</th>
                      <th>Side</th>
                      <th>Price</th>
                      <th>Count</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.resting_orders.map((o, i) => (
                      <tr key={i}>
                        <td><code style={{ fontSize: '0.75rem', background: '#2d3148', padding: '2px 6px', borderRadius: 4 }}>{o.ticker}</code></td>
                        <td>{o.action}</td>
                        <td>{o.side}</td>
                        <td>{fmt(o.price_usd)}</td>
                        <td>{o.count ?? '—'}</td>
                        <td>{o.status ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </div>

          <button className="btn btn-gray" onClick={refresh} disabled={loading}>
            {loading ? <span className="spinner" /> : null} Refresh
          </button>
        </>
      )}
    </div>
  )
}
