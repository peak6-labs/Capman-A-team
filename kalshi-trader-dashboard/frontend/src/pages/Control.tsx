import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { getControlStatus, setKillSwitch, setDryRun, getSignals, getCurrentTrades, sendChat, type ControlStatus, type SignalsResponse, type CurrentTrades, type ChatMessage } from '../api'
import { parseTicker } from '../tickerUtils'

function SideBadge({ side }: { side: string }) {
  const isYes = side.toLowerCase() === 'yes'
  return <span className={`side-badge ${isYes ? 'yes' : 'no'}`}>{side.toUpperCase()}</span>
}

function MarketCell({ ticker, title }: { ticker: string; title: string }) {
  const { label, detail } = parseTicker(ticker)
  const display = label !== ticker ? label : title
  return (
    <div>
      <div style={{ fontWeight: 500, fontSize: '0.8125rem' }}>{display}</div>
      {detail && <div className="muted" style={{ marginTop: 2 }}>{detail}</div>}
    </div>
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

  const [positions, setPositions] = useState<CurrentTrades | null>(null)

  const [chatHistories, setChatHistories] = useState<Record<'executor' | 'research', ChatMessage[]>>({ executor: [], research: [] })
  const [chatInput, setChatInput] = useState('')
  const [chatAgent, setChatAgent] = useState<'executor' | 'research'>('research')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const chatEndRef = useRef<HTMLDivElement>(null)

  const chatMessages = chatHistories[chatAgent]

  const switchAgent = (agent: 'executor' | 'research') => {
    setChatAgent(agent)
    setChatError(null)
  }

  const refresh = () => {
    setLoading(true)
    getControlStatus()
      .then(s => { setStatus(s); setError(null); setLastUpdated(new Date()) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  const loadSignals = () => {
    setSignalsLoading(true)
    getSignals()
      .then(s => { setSignals(s); setSignalsError(null) })
      .catch(e => setSignalsError(String(e)))
      .finally(() => setSignalsLoading(false))
  }

  const loadPositions = () => {
    getCurrentTrades()
      .then(d => setPositions(d))
      .catch(() => {})
  }

  useEffect(() => {
    refresh()
    loadPositions()
    const id = setInterval(() => { refresh(); loadPositions() }, 30_000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages])

  const sendMessage = async () => {
    const text = chatInput.trim()
    if (!text || chatBusy) return
    const agent = chatAgent
    const prev = chatHistories[agent]
    const userMsg: ChatMessage = { role: 'user', content: text }
    const nextHistory = [...prev, userMsg]
    setChatHistories(h => ({ ...h, [agent]: nextHistory }))
    setChatInput('')
    setChatBusy(true)
    setChatError(null)
    try {
      const { reply } = await sendChat(text, agent, prev, status?.username ?? 'operator')
      setChatHistories(h => ({ ...h, [agent]: [...nextHistory, { role: 'assistant', content: reply }] }))
    } catch (e) {
      setChatError(String(e))
    }
    setChatBusy(false)
  }

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

          {/* Active positions tile */}
          <div className="card" style={{ marginTop: '1.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
              <h2 style={{ margin: 0 }}>
                Active Positions
                {positions && (
                  <span className="muted" style={{ fontWeight: 400, fontSize: '0.875rem', marginLeft: '0.5rem' }}>
                    · {positions.open_positions.length}
                  </span>
                )}
              </h2>
              <button className="btn btn-gray" onClick={loadPositions} style={{ fontSize: '0.8rem', padding: '0.25rem 0.75rem' }}>
                Refresh
              </button>
            </div>
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
                        <td><MarketCell ticker={p.ticker} title={p.ticker} /></td>
                        <td>
                          {p.position != null ? (
                            <span className={`side-badge ${parseFloat(p.position) >= 0 ? 'yes' : 'no'}`}>
                              {parseFloat(p.position) >= 0 ? 'Yes' : 'No'} · {Math.abs(parseFloat(p.position))}
                            </span>
                          ) : '—'}
                        </td>
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
        </>
      )}

      {/* Live Agent Queue */}
      <div className="card" style={{ marginTop: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
          <h2 style={{ margin: 0 }}>
            Live Agent Queue
            {signals && (
              <span className="muted" style={{ fontWeight: 400, fontSize: '0.875rem', marginLeft: '0.5rem' }}>
                · {signals.candidates.length} candidates from {signals.total_scanned} scanned
              </span>
            )}
          </h2>
          <button className="btn btn-gray" onClick={loadSignals} disabled={signalsLoading} style={{ fontSize: '0.8rem', padding: '0.25rem 0.75rem' }}>
            {signalsLoading ? <span className="spinner" /> : 'Scan now'}
          </button>
        </div>

        {signalsError && <p className="error">{signalsError}</p>}

        {!signals && !signalsLoading ? (
          <p className="muted">Click "Scan now" to screen open markets for candidates.</p>
        ) : signalsLoading && !signals ? (
          <p className="muted">Scanning markets…</p>
        ) : signals && signals.candidates.length === 0 ? (
          <p className="muted">No candidates found matching cheap-tail criteria (1–10¢, 4–48h expiry).</p>
        ) : signals ? (
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
                <th>Volume</th>
              </tr>
            </thead>
            <tbody>
              {signals.candidates.map((c, i) => (
                <tr key={i}>
                  <td><MarketCell ticker={c.ticker} title={c.title} /></td>
                  <td><span className="badge badge-gray">{c.category ?? '—'}</span></td>
                  <td><SideBadge side={c.side} /></td>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(parseFloat(c.price) * 100).toFixed(1)}¢</td>
                  <td className="muted" style={{ fontVariantNumeric: 'tabular-nums' }}>{(parseFloat(c.spread) * 100).toFixed(1)}¢</td>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>{c.score.toFixed(3)}</td>
                  <td className="muted">{c.hours_to_expiry}h</td>
                  <td className="muted">{c.volume_fp.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}

        {signals && (
          <p className="muted" style={{ marginTop: '0.5rem', fontSize: '0.75rem' }}>
            Score = price × hours. Cached 60s. Results pass compliance + liquidity + time filters.
          </p>
        )}
      </div>

      {/* Agent chat */}
      <div className="card chat-panel" style={{ marginTop: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
          <h2 style={{ margin: 0 }}>Agent Chat</h2>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className={`btn ${chatAgent === 'executor' ? 'btn-brand' : 'btn-gray'}`}
              style={{ fontSize: '0.75rem', padding: '0.25rem 0.75rem' }}
              onClick={() => switchAgent('executor')}
            >
              Executor
            </button>
            <button
              className={`btn ${chatAgent === 'research' ? 'btn-brand' : 'btn-gray'}`}
              style={{ fontSize: '0.75rem', padding: '0.25rem 0.75rem' }}
              onClick={() => switchAgent('research')}
            >
              Research
            </button>
          </div>
        </div>

        <div className="chat-messages">
          {chatMessages.length === 0 && (
            <p className="muted" style={{ fontSize: '0.8125rem', fontStyle: 'italic' }}>
              Ask the {chatAgent} agent anything…
            </p>
          )}
          {chatMessages.map((m, i) => (
            <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
              <span className="chat-role">{m.role === 'user' ? (status?.username ?? 'you') : chatAgent === 'executor' ? 'Executor' : 'Research'}</span>
              {m.role === 'assistant' ? (
                <div className="chat-content chat-md">
                  <ReactMarkdown>{m.content}</ReactMarkdown>
                </div>
              ) : (
                <span className="chat-content">{m.content}</span>
              )}
            </div>
          ))}
          {chatBusy && (
            <div className="chat-bubble chat-bubble-assistant">
              <span className="chat-role">{chatAgent === 'executor' ? 'Executor' : 'Research'}</span>
              <span className="muted" style={{ fontStyle: 'italic' }}>thinking…</span>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {chatError && <p className="error" style={{ marginTop: '0.5rem', fontSize: '0.8125rem' }}>{chatError}</p>}

        <div className="chat-input-row">
          <input
            className="chat-input"
            type="text"
            placeholder={`Message ${chatAgent} agent…`}
            value={chatInput}
            onChange={e => setChatInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') sendMessage() }}
            disabled={chatBusy}
          />
          <button className="btn btn-brand" onClick={sendMessage} disabled={chatBusy || !chatInput.trim()}>
            {chatBusy ? <span className="spinner" /> : 'Send →'}
          </button>
        </div>
      </div>
    </div>
  )
}
