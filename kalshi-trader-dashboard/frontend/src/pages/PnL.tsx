import { useCallback, useEffect, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts'
import {
  getPnlSummary, getPnlTimeseries, getPnlCalibration,
  type PnlSummary, type PnlTimeseries, type PnlCalibration
} from '../api'

function fmt(v: string | null | undefined) {
  if (v == null) return '—'
  const n = parseFloat(v)
  if (isNaN(n)) return v
  const sign = n > 0 ? '+' : ''
  return `${sign}$${n.toFixed(2)}`
}

function fmtClass(v: string | null | undefined) {
  if (!v) return ''
  const n = parseFloat(v)
  if (isNaN(n)) return ''
  return n >= 0 ? 'pos' : 'neg'
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

export default function PnL() {
  const [summary, setSummary] = useState<PnlSummary | null>(null)
  const [series, setSeries] = useState<PnlTimeseries | null>(null)
  const [calibration, setCalibration] = useState<PnlCalibration | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'overview' | 'calibration'>('overview')
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const refresh = () => {
    setLoading(true)
    Promise.all([getPnlSummary(), getPnlTimeseries()])
      .then(([s, ts]) => { setSummary(s); setSeries(ts); setError(null); setLastUpdated(new Date()) })
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

  const chartData = series?.points.map(p => ({
    time: new Date(p.ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    pnl: parseFloat(p.cumulative_realized_usd),
  })) ?? []

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
              {/* Inline stat row */}
              <div style={{ display: 'flex', gap: '2.5rem', marginBottom: '1.75rem', alignItems: 'flex-start' }}>
                <div>
                  <div className="stat-label">Realized PnL</div>
                  <div className={`stat-value ${fmtClass(summary.realized_usd)}`}>{fmt(summary.realized_usd)}</div>
                </div>
                <div>
                  <div className="stat-label">Unrealized PnL</div>
                  <div className={`stat-value ${fmtClass(summary.unrealized_usd)}`}>{fmt(summary.unrealized_usd)}</div>
                </div>
                <div>
                  <div className="stat-label">Total PnL</div>
                  <div className={`stat-value ${fmtClass(summary.total_usd)}`}>{fmt(summary.total_usd)}</div>
                </div>
              </div>

              <div className="card">
                <h2>Cumulative Realized PnL</h2>
                {chartData.length === 0
                  ? <p className="muted">No fill history to chart yet.</p>
                  : (
                    <ResponsiveContainer width="100%" height={280}>
                      <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                        <XAxis dataKey="time" tick={{ fontSize: 11, fill: 'var(--chart-tick)' }} />
                        <YAxis tick={{ fontSize: 11, fill: 'var(--chart-tick)' }} tickFormatter={v => `$${v.toFixed(2)}`} />
                        <Tooltip
                          contentStyle={{
                            background: 'var(--chart-tooltip-bg)',
                            border: '1px solid var(--chart-tooltip-border)',
                            borderRadius: 8,
                            color: 'var(--text)',
                          }}
                          labelStyle={{ color: 'var(--label)', fontSize: 11 }}
                          formatter={(v) => [`$${Number(v ?? 0).toFixed(2)}`, 'Cumulative PnL']}
                        />
                        <Line
                          type="monotone"
                          dataKey="pnl"
                          stroke="var(--pos)"
                          strokeWidth={2}
                          dot={false}
                          activeDot={{ r: 4, fill: 'var(--pos)' }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                {series && (
                  <p className="muted" style={{ marginTop: '0.75rem' }}>
                    Current unrealized: {fmt(series.current_unrealized_usd)}&ensp;·&ensp;
                    {chartData.length} fill event{chartData.length !== 1 ? 's' : ''}
                  </p>
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
