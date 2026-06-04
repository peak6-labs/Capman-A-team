import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { getControlStatus, setKillSwitch, setDryRun, getSignals, getCurrentTrades, sendChat, type ControlStatus, type SignalsResponse, type CurrentTrades, type ChatMessage, type ChatImageAttachment } from '../api'
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

  const [positions, setPositions] = useState<CurrentTrades | null>(null)

  const [chatHistories, setChatHistories] = useState<Record<'executor' | 'research', ChatMessage[]>>({ executor: [], research: [] })
  const [chatInput, setChatInput] = useState('')
  const [chatAgent, setChatAgent] = useState<'executor' | 'research'>('research')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [attachedImages, setAttachedImages] = useState<Array<{ preview: string; attachment: ChatImageAttachment }>>([])
  const chatEndRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

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

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    e.target.value = ''
    files.forEach(file => {
      const reader = new FileReader()
      reader.onload = ev => {
        const dataUrl = ev.target?.result as string
        const [header, data] = dataUrl.split(',')
        const media_type = header.match(/:(.*?);/)?.[1] ?? 'image/jpeg'
        setAttachedImages(prev => [...prev, { preview: dataUrl, attachment: { media_type, data } }])
      }
      reader.readAsDataURL(file)
    })
  }

  const sendMessage = async () => {
    const text = chatInput.trim()
    if ((!text && attachedImages.length === 0) || chatBusy) return
    const agent = chatAgent
    const prev = chatHistories[agent]
    const previews = attachedImages.map(a => a.preview)
    const images = attachedImages.map(a => a.attachment)
    const userMsg: ChatMessage = { role: 'user', content: text, imagePreviews: previews.length ? previews : undefined }
    const nextHistory = [...prev, userMsg]
    setChatHistories(h => ({ ...h, [agent]: nextHistory }))
    setChatInput('')
    setAttachedImages([])
    setChatBusy(true)
    setChatError(null)
    try {
      const { reply } = await sendChat(text, agent, prev, status?.username ?? 'operator', images)
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

          {/* Live Agent Queue */}
          <div className="card">
            <div className="home-card-header">
              <h2 style={{ margin: 0 }}>
                Live Agent Queue
                {signals && (
                  <span className="home-count-chip">{signals.candidates.length}</span>
                )}
              </h2>
              <button className="btn btn-gray home-card-btn" onClick={loadSignals} disabled={signalsLoading}>
                {signalsLoading ? <span className="spinner" /> : 'Scan Now'}
              </button>
            </div>

            {signalsError && <p className="error">{signalsError}</p>}

            {!signals && !signalsLoading ? (
              <p className="muted">Click "Scan Now" to screen open markets for candidates.</p>
            ) : signalsLoading && !signals ? (
              <p className="muted">Scanning markets…</p>
            ) : signals && signals.candidates.length === 0 ? (
              <p className="muted">No candidates matching cheap-tail criteria (1–10¢, 4–48h expiry).</p>
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
                          <td><MarketCell ticker={c.ticker} title={c.title} /></td>
                          <td><span className="badge badge-gray">{c.category ?? '—'}</span></td>
                          <td><SideBadge side={c.side} /></td>
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
                  Score = price × hours · cached 60s · passes compliance + liquidity + time filters
                </p>
              </>
            ) : null}
          </div>
        </div>

        {/* ── Right column: Agent Chat ── */}
        <div className="home-right">
          <div className="card home-chat-card">
            <div className="home-card-header" style={{ marginBottom: '0.75rem' }}>
              <h2 style={{ margin: 0 }}>Agent Chat</h2>
              <div style={{ display: 'flex', gap: '0.375rem' }}>
                <button
                  className={`btn home-agent-btn ${chatAgent === 'research' ? 'home-agent-btn-active' : ''}`}
                  onClick={() => switchAgent('research')}
                >
                  Research
                </button>
                <button
                  className={`btn home-agent-btn ${chatAgent === 'executor' ? 'home-agent-btn-active' : ''}`}
                  onClick={() => switchAgent('executor')}
                >
                  Executor
                </button>
              </div>
            </div>

            <div className="chat-messages home-chat-messages">
              {chatMessages.length === 0 && (
                <div className="home-chat-empty">
                  <span style={{ fontSize: '1.5rem', opacity: 0.3 }}>
                    {chatAgent === 'research' ? '🔬' : '⚡'}
                  </span>
                  <p className="muted" style={{ fontSize: '0.8125rem', fontStyle: 'italic', marginTop: '0.5rem' }}>
                    Ask the {chatAgent} agent anything…
                  </p>
                </div>
              )}
              {chatMessages.map((m, i) => (
                <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
                  <span className="chat-role">
                    {m.role === 'user' ? (status?.username ?? 'you') : chatAgent === 'executor' ? 'Executor' : 'Research'}
                  </span>
                  {m.imagePreviews && m.imagePreviews.length > 0 && (
                    <div className="chat-image-previews">
                      {m.imagePreviews.map((src, j) => (
                        <img key={j} src={src} className="chat-bubble-image" alt="attached" />
                      ))}
                    </div>
                  )}
                  {m.role === 'assistant' ? (
                    <div className="chat-content chat-md">
                      <ReactMarkdown>{m.content}</ReactMarkdown>
                    </div>
                  ) : (
                    m.content ? <span className="chat-content">{m.content}</span> : null
                  )}
                </div>
              ))}
              {chatBusy && (
                <div className="chat-bubble chat-bubble-assistant">
                  <span className="chat-role">{chatAgent === 'executor' ? 'Executor' : 'Research'}</span>
                  <span className="chat-content home-thinking">
                    <span className="home-thinking-dot" /><span className="home-thinking-dot" /><span className="home-thinking-dot" />
                  </span>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {chatError && <p className="error" style={{ marginTop: '0.5rem', fontSize: '0.8125rem' }}>{chatError}</p>}

            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: 'none' }}
              onChange={handleFileChange}
            />
            {attachedImages.length > 0 && (
              <div className="chat-attach-preview-row">
                {attachedImages.map((img, i) => (
                  <div key={i} className="chat-attach-thumb-wrap">
                    <img src={img.preview} className="chat-attach-thumb" alt="attachment" />
                    <button
                      className="chat-attach-remove"
                      onClick={() => setAttachedImages(prev => prev.filter((_, j) => j !== i))}
                      title="Remove"
                    >✕</button>
                  </div>
                ))}
              </div>
            )}
            <div className="chat-input-row">
              <button
                className="btn btn-gray chat-attach-btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={chatBusy}
                title="Attach image"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66L9.42 16.41a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                </svg>
              </button>
              <input
                className="chat-input"
                type="text"
                placeholder={`Message ${chatAgent} agent…`}
                value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') sendMessage() }}
                disabled={chatBusy}
              />
              <button
                className="btn btn-brand"
                onClick={sendMessage}
                disabled={chatBusy || (!chatInput.trim() && attachedImages.length === 0)}
              >
                {chatBusy ? <span className="spinner" /> : 'Send →'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
