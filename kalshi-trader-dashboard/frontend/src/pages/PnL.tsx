import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bar, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import {
  getPnlCalibration, getPnlDaily, getPnlSummary, getPnlTrades,
  type PnlCalibration, type PnlDaily, type PnlSummary, type PnlTrade, type PnlTrades,
} from '../api'
import MarketCell from '../components/MarketCell'

function fmt(v: string | null | undefined) {
  if (v == null) return '—'
  const n = parseFloat(v)
  if (isNaN(n)) return v
  const sign = n > 0 ? '+' : ''
  return `${sign}$${n.toFixed(2)}`
}

function fmtMoney(v: string | null | undefined) {
  if (v == null) return '—'
  const n = parseFloat(v)
  if (isNaN(n)) return v
  return `$${n.toFixed(2)}`
}

function fmtCount(v: string | null | undefined) {
  if (v == null) return '—'
  const n = parseFloat(v)
  if (isNaN(n)) return v
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

function fmtClass(v: string | null | undefined) {
  if (!v) return ''
  const n = parseFloat(v)
  if (isNaN(n)) return ''
  return n >= 0 ? 'pos' : 'neg'
}

function dateFromIso(v: string) {
  return new Date(`${v}T00:00:00`)
}

function dayLabel(v: string) {
  return dateFromIso(v).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function CalibBucket({ b }: { b: { label: string; count: number; brier: number | null; mean_predicted: number | null; mean_realized: number | null } }) {
  const f3 = (v: number | null) => v == null ? '—' : v.toFixed(3)
  return (
    <tr>
      <td>{b.label}</td>
      <td>{b.count}</td>
      <td>{f3(b.brier)}</td>
      <td>{f3(b.mean_predicted)}</td>
      <td>{f3(b.mean_realized)}</td>
    </tr>
  )
}

function signedReturn(trade: PnlTrade) {
  const amount = fmt(trade.total_return_usd)
  const pct = parseFloat(trade.total_return_pct)
  if (isNaN(pct)) return amount
  const sign = pct > 0 ? '+' : ''
  return `${amount} (${sign}${pct.toFixed(0)}%)`
}

function sideClass(side: string | null | undefined) {
  return side?.toLowerCase() === 'yes' ? 'yes' : 'no'
}

export default function PnL() {
  const [summary, setSummary] = useState<PnlSummary | null>(null)
  const [daily, setDaily] = useState<PnlDaily | null>(null)
  const [trades, setTrades] = useState<PnlTrades | null>(null)
  const [calibration, setCalibration] = useState<PnlCalibration | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'overview' | 'calibration'>('overview')
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const refresh = () => {
    setLoading(true)
    Promise.all([getPnlSummary(), getPnlDaily(), getPnlTrades()])
      .then(([summaryData, dailyData, tradeData]) => {
        setSummary(summaryData)
        setDaily(dailyData)
        setTrades(tradeData)
        setError(null)
        setLastUpdated(new Date())
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  const loadCalibration = useCallback(() => {
    if (calibration) return
    setLoading(true)
    getPnlCalibration()
      .then(c => { setCalibration(c); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [calibration])

  useEffect(() => {
    queueMicrotask(refresh)
    const id = setInterval(refresh, 30_000)
    return () => clearInterval(id)
  }, [])
  useEffect(() => {
    if (tab === 'calibration') queueMicrotask(loadCalibration)
  }, [tab, loadCalibration])

  const chartData = useMemo(() => (
    daily?.points.map(p => ({
      day: dayLabel(p.date),
      daily: parseFloat(p.realized_usd),
      cumulative: parseFloat(p.cumulative_realized_usd),
    })) ?? []
  ), [daily])

  const profitableDays = chartData.filter(p => p.daily > 0).length
  const losingDays = chartData.filter(p => p.daily < 0).length
  const historyGroups = useMemo(() => {
    const map = new Map<string, PnlTrade[]>()
    for (const trade of trades?.trades ?? []) {
      const key = trade.group_title || trade.title || trade.ticker
      map.set(key, [...(map.get(key) ?? []), trade])
    }
    return Array.from(map.entries()).map(([title, rows]) => ({
      title,
      rows,
      lastOrderAt: Math.max(...rows.map(row => row.last_order_at || 0)),
      settlement: rows.reduce((sum, row) => sum + parseFloat(row.settlement_payout_usd ?? '0'), 0),
      cost: rows.reduce((sum, row) => sum + parseFloat(row.total_cost_usd ?? '0'), 0),
      payout: rows.reduce((sum, row) => sum + parseFloat(row.total_payout_usd ?? '0'), 0),
      totalReturn: rows.reduce((sum, row) => sum + parseFloat(row.total_return_usd ?? '0'), 0),
    })).sort((a, b) => b.lastOrderAt - a.lastOrderAt)
  }, [trades])

  return (
    <div className="page">
      <h1>PnL</h1>
      {error && <p className="error">{error}</p>}

      <div className="tabs-line">
        <button className={`tab-line ${tab === 'overview' ? 'active' : ''}`} onClick={() => setTab('overview')}>
          Overview
        </button>
        <button className={`tab-line ${tab === 'calibration' ? 'active' : ''}`} onClick={() => setTab('calibration')}>
          Calibration
        </button>
      </div>

      {tab === 'overview' && (
        <>
          {loading && !summary ? <span className="spinner" /> : summary && (
            <>
              <div className="card pnl-chart-card">
                <div className="pnl-chart-header">
                  <div>
                    <h2>Daily PnL Since {daily ? dayLabel(daily.start_date) : 'Jun 1'}</h2>
                    <div className="pnl-chart-substats">
                      <span>Started {fmtMoney(summary.starting_bankroll_usd)}</span>
                      <span>Account {fmtMoney(summary.account_value_usd)}</span>
                      <span>{profitableDays} up day{profitableDays !== 1 ? 's' : ''}</span>
                      <span>{losingDays} down day{losingDays !== 1 ? 's' : ''}</span>
                    </div>
                  </div>
                  <div className="pnl-stat-strip">
                    <div>
                      <div className="stat-label">Cash PnL</div>
                      <div className={`stat-value compact ${fmtClass(summary.realized_usd)}`}>{fmt(summary.realized_usd)}</div>
                    </div>
                    <div>
                      <div className="stat-label">Open Value</div>
                      <div className={`stat-value compact ${fmtClass(summary.unrealized_usd)}`}>{fmt(summary.unrealized_usd)}</div>
                    </div>
                    <div>
                      <div className="stat-label">Total PnL</div>
                      <div className={`stat-value compact ${fmtClass(summary.total_usd)}`}>{fmt(summary.total_usd)}</div>
                    </div>
                  </div>
                </div>

                {chartData.length === 0
                  ? <p className="muted">No fill history to chart yet.</p>
                  : (
                    <ResponsiveContainer width="100%" height={320}>
                      <ComposedChart data={chartData} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                        <XAxis dataKey="day" tick={{ fontSize: 11, fill: 'var(--chart-tick)' }} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11, fill: 'var(--chart-tick)' }} tickFormatter={v => `$${Number(v).toFixed(0)}`} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: 'var(--chart-tick)' }} tickFormatter={v => `$${Number(v).toFixed(0)}`} />
                        <Tooltip
                          contentStyle={{
                            background: 'var(--chart-tooltip-bg)',
                            border: '1px solid var(--chart-tooltip-border)',
                            borderRadius: 8,
                            color: 'var(--text)',
                          }}
                          labelStyle={{ color: 'var(--label)', fontSize: 11 }}
                          formatter={(value, name) => [
                            `$${Number(value ?? 0).toFixed(2)}`,
                            name === 'daily' ? 'Daily PnL' : 'Cumulative PnL',
                          ]}
                        />
                        <ReferenceLine yAxisId="left" y={0} stroke="var(--border2)" />
                        <Bar yAxisId="left" dataKey="daily" radius={[4, 4, 0, 0]}>
                          {chartData.map((point, index) => (
                            <Cell key={`cell-${index}`} fill={point.daily >= 0 ? 'var(--pos)' : 'var(--neg)'} />
                          ))}
                        </Bar>
                        <Line
                          yAxisId="right"
                          type="monotone"
                          dataKey="cumulative"
                          stroke="var(--brand)"
                          strokeWidth={2}
                          dot={{ r: 3, fill: 'var(--brand)' }}
                          activeDot={{ r: 5, fill: 'var(--brand)' }}
                        />
                      </ComposedChart>
                    </ResponsiveContainer>
                  )}
              </div>

              <div className="card pnl-history-card">
                <h2>History ({trades?.trades.length ?? 0})</h2>
                {!trades || trades.trades.length === 0
                  ? <p className="muted">No portfolio history on record.</p>
                  : (
                    <div className="table-scroll">
                      <table className="pnl-history-table">
                        <thead>
                          <tr>
                            <th>Market</th>
                            <th>Final position</th>
                            <th>Settlement payout</th>
                            <th>Total cost</th>
                            <th>Total payout</th>
                            <th>Total return</th>
                          </tr>
                        </thead>
                        <tbody>
                          {historyGroups.map(group => {
                            const groupPct = group.cost ? group.totalReturn / group.cost * 100 : 0
                            if (group.rows.length === 1) {
                              const trade = group.rows[0]
                              return (
                                <tr key={group.title} className="pnl-history-row">
                                  <td>
                                    <MarketCell ticker={trade.ticker} name={trade.name} title={group.title} />
                                  </td>
                                  <td className={sideClass(trade.held_side)}>
                                    {fmtCount(trade.entry_count)} {trade.held_side ? trade.held_side.toUpperCase() : '—'}
                                  </td>
                                  <td className={fmtClass(trade.settlement_payout_usd)}>{fmt(trade.settlement_payout_usd)}</td>
                                  <td>{fmt(trade.total_cost_usd)}</td>
                                  <td>{fmt(trade.total_payout_usd)}</td>
                                  <td className={fmtClass(trade.total_return_usd)}>{signedReturn(trade)}</td>
                                </tr>
                              )
                            }
                            return (
                              <Fragment key={group.title}>
                                <tr className="pnl-history-group">
                                  <td colSpan={6}>
                                    <MarketCell ticker={group.rows[0]?.ticker} title={group.title} />
                                  </td>
                                </tr>
                                {group.rows.map(trade => (
                                  <tr key={`${trade.ticker}-${trade.held_side ?? 'side'}`} className="pnl-history-row">
                                    <td>
                                      <div>{trade.name || trade.ticker}</div>
                                      <div className="muted">{trade.status === 'open' ? 'Open' : trade.settlement_result ? `${trade.settlement_result.toUpperCase()} settled` : 'Closed'}</div>
                                    </td>
                                    <td className={sideClass(trade.held_side)}>
                                      {fmtCount(trade.entry_count)} {trade.held_side ? trade.held_side.toUpperCase() : '—'}
                                    </td>
                                    <td className={fmtClass(trade.settlement_payout_usd)}>{fmt(trade.settlement_payout_usd)}</td>
                                    <td>{fmt(trade.total_cost_usd)}</td>
                                    <td>{fmt(trade.total_payout_usd)}</td>
                                    <td className={fmtClass(trade.total_return_usd)}>{signedReturn(trade)}</td>
                                  </tr>
                                ))}
                                <tr className="pnl-history-total">
                                  <td>Total</td>
                                  <td />
                                  <td>{fmt(String(group.settlement))}</td>
                                  <td>{fmt(String(group.cost))}</td>
                                  <td>{fmt(String(group.payout))}</td>
                                  <td className={group.totalReturn >= 0 ? 'pos' : 'neg'}>
                                    {fmt(String(group.totalReturn))} ({groupPct > 0 ? '+' : ''}{groupPct.toFixed(0)}%)
                                  </td>
                                </tr>
                              </Fragment>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
              </div>
            </>
          )}
          <div style={{ marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <button className="btn btn-gray" onClick={refresh} disabled={loading}>
              {loading ? <span className="spinner" /> : null} Refresh
            </button>
            {lastUpdated && (
              <span className="muted">Updated {lastUpdated.toLocaleTimeString()} · auto-refreshes every 30s</span>
            )}
          </div>
        </>
      )}

      {tab === 'calibration' && (
        <>
          {loading && !calibration ? <span className="spinner" /> : calibration && (
            <>
              <div className="card" style={{ marginBottom: '0.75rem' }}>
                <p className="muted">
                  Scored {calibration.scored} position{calibration.scored !== 1 ? 's' : ''}&ensp;·&ensp;
                  {calibration.skipped_unsettled} unsettled&ensp;·&ensp;
                  {calibration.skipped_no_prediction} no prediction
                </p>
              </div>

              {calibration.scored === 0 ? (
                <div className="card"><p className="muted">No closed positions scored yet.</p></div>
              ) : (
                <>
                  <div className="card">
                    <h2>Overall</h2>
                    <table>
                      <thead><tr><th>Bucket</th><th>n</th><th>Brier</th><th>Mean Predicted</th><th>Mean Realized</th></tr></thead>
                      <tbody><CalibBucket b={calibration.overall} /></tbody>
                    </table>
                  </div>
                  {calibration.by_source.length > 0 && (
                    <div className="card">
                      <h2>By Source</h2>
                      <table>
                        <thead><tr><th>Bucket</th><th>n</th><th>Brier</th><th>Mean Predicted</th><th>Mean Realized</th></tr></thead>
                        <tbody>{calibration.by_source.map((b, i) => <CalibBucket key={i} b={b} />)}</tbody>
                      </table>
                    </div>
                  )}
                  {calibration.by_category.length > 0 && (
                    <div className="card">
                      <h2>By Category</h2>
                      <table>
                        <thead><tr><th>Bucket</th><th>n</th><th>Brier</th><th>Mean Predicted</th><th>Mean Realized</th></tr></thead>
                        <tbody>{calibration.by_category.map((b, i) => <CalibBucket key={i} b={b} />)}</tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
