import { useEffect, useState } from 'react'
import { getPortfolio, type PortfolioResponse, type Position } from '../api'
import { parseTicker } from '../tickerUtils'

function MarketCell({ ticker }: { ticker: string | null | undefined }) {
  const { label, detail } = parseTicker(ticker)
  return (
    <div>
      <div style={{ fontWeight: 500, fontSize: '0.8125rem' }}>{label}</div>
      {detail && <div className="muted" style={{ marginTop: 2 }}>{detail}</div>}
    </div>
  )
}

function fmt(v: string | null | undefined, sign = false) {
  if (v == null) return '—'
  const n = parseFloat(v)
  if (isNaN(n)) return v
  const prefix = sign && n > 0 ? '+' : ''
  return `${prefix}$${Math.abs(n).toFixed(2)}`
}

function fmtPct(pnl: string | null | undefined, cost: string | null | undefined) {
  if (!pnl || !cost) return ''
  const p = parseFloat(pnl)
  const c = parseFloat(cost)
  if (isNaN(p) || isNaN(c) || c === 0) return ''
  return ` (${((p / Math.abs(c)) * 100).toFixed(1)}%)`
}

function pnlClass(v: string | null | undefined) {
  if (!v) return ''
  const n = parseFloat(v)
  if (isNaN(n)) return ''
  return n >= 0 ? 'pos' : 'neg'
}

function totalReturn(p: Position) {
  const r = parseFloat(p.realized_pnl_usd ?? '0') || 0
  const u = parseFloat(p.unrealized_pnl_usd ?? '0') || 0
  return r + u
}

function avgPrice(p: Position) {
  const cost = parseFloat(p.cost_usd ?? '0') || 0
  const pos = Math.abs(parseFloat(p.position ?? '0') || 0)
  if (pos === 0) return null
  return cost / pos
}

function sumField(positions: Position[], field: keyof Position) {
  return positions.reduce((acc, p) => {
    const v = parseFloat((p[field] as string | null) ?? '0') || 0
    return acc + v
  }, 0)
}

function SideChip({ position }: { position: string | null }) {
  if (!position) return <span className="muted">—</span>
  const n = parseFloat(position)
  if (isNaN(n)) return <span>{position}</span>
  const isYes = n > 0
  return (
    <span className={`side-badge ${isYes ? 'yes' : 'no'}`}>
      {isYes ? 'Yes' : 'No'} · {Math.abs(n)}
    </span>
  )
}

export default function Portfolio() {
  const [data, setData] = useState<PortfolioResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'positions' | 'orders'>('positions')
  const [search, setSearch] = useState('')
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const refresh = () => {
    setLoading(true)
    getPortfolio()
      .then(d => { setData(d); setError(null); setLastUpdated(new Date()) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30_000)
    return () => clearInterval(id)
  }, [])

  const portfolioVal = data ? parseFloat(data.portfolio_value_usd ?? '0') || 0 : 0
  const cashVal = data ? parseFloat(data.cash_balance_usd ?? '0') || 0 : 0

  const totalUnrealized = data ? sumField(data.open_positions, 'unrealized_pnl_usd') : 0
  const totalExposure = data ? sumField(data.open_positions, 'exposure_usd') : 0
  const totalCost = data ? sumField(data.open_positions, 'cost_usd') : 0

  const changeAmt = totalUnrealized
  const changePct = totalCost !== 0 ? (totalUnrealized / Math.abs(totalCost)) * 100 : 0
  const changePos = changeAmt >= 0

  const filteredPositions = (data?.open_positions ?? []).filter(p =>
    !search || (p.ticker ?? '').toLowerCase().includes(search.toLowerCase())
  )

  const positionsTotalReturn = filteredPositions.reduce((acc, p) => acc + totalReturn(p), 0)
  const positionsTotalCost = filteredPositions.reduce((acc, p) => acc + (parseFloat(p.cost_usd ?? '0') || 0), 0)

  return (
    <div className="page">
      {error && <p className="error">{error}</p>}

      {/* Hero */}
      {data && (
        <div className="portfolio-hero">
          <div className="portfolio-hero-left">
            <div className="portfolio-title">Portfolio</div>
            <div className="portfolio-value">${portfolioVal.toFixed(2)}</div>
            {changeAmt !== 0 && (
              <div className={`change-chip ${changePos ? 'pos' : 'neg'}`}>
                <span className="arrow">{changePos ? '▲' : '▼'}</span>
                <span>
                  ${Math.abs(changeAmt).toFixed(2)} ({Math.abs(changePct).toFixed(2)}%) unrealized
                </span>
              </div>
            )}
          </div>
          <div className="portfolio-hero-right">
            <div className="stat-inline">
              <span className="stat-inline-label">Positions</span>
              <span className="stat-inline-value">${totalExposure.toFixed(2)}</span>
            </div>
            <div className="stat-inline">
              <span className="stat-inline-label">Cash</span>
              <span className="stat-inline-value">${cashVal.toFixed(2)}</span>
            </div>
            <div className="stat-inline">
              <span className="stat-inline-label">Open</span>
              <span className="stat-inline-value">{data.open_positions.length}</span>
            </div>
          </div>
        </div>
      )}

      {loading && !data && <span className="spinner" />}

      {data && (
        <>
          {/* Underline tabs */}
          <div className="tabs-line">
            <button
              className={`tab-line ${tab === 'positions' ? 'active' : ''}`}
              onClick={() => setTab('positions')}
            >
              Positions
            </button>
            <button
              className={`tab-line ${tab === 'orders' ? 'active' : ''}`}
              onClick={() => setTab('orders')}
            >
              Orders {data.resting_orders.length > 0 && `(${data.resting_orders.length})`}
            </button>
          </div>

          {tab === 'positions' && (
            <>
              <div className="search-wrap">
                <input
                  className="search-input"
                  placeholder="Search positions"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                />
              </div>

              {filteredPositions.length === 0 ? (
                <p className="muted">{search ? 'No positions match your search.' : 'No open positions.'}</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Market</th>
                      <th>Position</th>
                      <th>Avg Price</th>
                      <th>Cost</th>
                      <th>Realized PnL</th>
                      <th>Unrealized PnL</th>
                      <th>Total Return</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredPositions.map((p, i) => {
                      const avg = avgPrice(p)
                      const ret = totalReturn(p)
                      const retClass = ret >= 0 ? 'pos' : 'neg'
                      const retSign = ret >= 0 ? '+' : '-'
                      const retPct = fmtPct(String(ret), p.cost_usd)
                      return (
                        <tr key={i}>
                          <td><MarketCell ticker={p.ticker} /></td>
                          <td><SideChip position={p.position} /></td>
                          <td>{avg != null ? `${(avg * 100).toFixed(1)}¢` : '—'}</td>
                          <td>{fmt(p.cost_usd)}</td>
                          <td className={pnlClass(p.realized_pnl_usd)}>{fmt(p.realized_pnl_usd, true)}</td>
                          <td className={pnlClass(p.unrealized_pnl_usd)}>{fmt(p.unrealized_pnl_usd, true)}</td>
                          <td className={retClass}>
                            {retSign}${Math.abs(ret).toFixed(2)}{retPct}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                  {filteredPositions.length > 1 && (
                    <tfoot>
                      <tr className="table-total">
                        <td colSpan={3}>Total</td>
                        <td>${positionsTotalCost.toFixed(2)}</td>
                        <td />
                        <td />
                        <td className={positionsTotalReturn >= 0 ? 'pos' : 'neg'}>
                          {positionsTotalReturn >= 0 ? '+' : '-'}${Math.abs(positionsTotalReturn).toFixed(2)}
                          {positionsTotalCost !== 0
                            ? ` (${((positionsTotalReturn / Math.abs(positionsTotalCost)) * 100).toFixed(1)}%)`
                            : ''}
                        </td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              )}
            </>
          )}

          {tab === 'orders' && (
            data.resting_orders.length === 0 ? (
              <p className="muted">No resting orders.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Market</th>
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
                      <td><MarketCell ticker={o.ticker} /></td>
                      <td>{o.action}</td>
                      <td>
                        {o.side
                          ? <span className={`side-badge ${o.side.toLowerCase() === 'yes' ? 'yes' : 'no'}`}>{o.side}</span>
                          : '—'}
                      </td>
                      <td>{fmt(o.price_usd)}</td>
                      <td>{o.count ?? '—'}</td>
                      <td>
                        {o.status
                          ? <span className="badge badge-gray">{o.status}</span>
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}

          <div style={{ marginTop: '1.25rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <button className="btn btn-gray" onClick={refresh} disabled={loading}>
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
