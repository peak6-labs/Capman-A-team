import { parseTicker } from '../tickerUtils'

interface MarketCellProps {
  ticker: string | null | undefined
  // Authoritative values from Kalshi market data (preferred over ticker parsing).
  name?: string | null   // held-side contract/player name, e.g. "Arnaldi"
  title?: string | null  // market title, e.g. "Who will win the French Open (Men)?"
  // Legacy fallback: shown only when ticker parsing can't resolve a label.
  fallbackTitle?: string | null
}

export default function MarketCell({ ticker, name, title, fallbackTitle }: MarketCellProps) {
  // Prefer authoritative API names; fall back to ticker-suffix parsing only when
  // the API gave us nothing (e.g. settled markets no longer returned).
  if (name || title) {
    const primary = name || title
    const detail = name && title && title !== name ? title : ''
    return (
      <div>
        <div style={{ fontWeight: 500, fontSize: '0.8125rem' }}>{primary}</div>
        {detail && <div className="muted" style={{ marginTop: 2 }}>{detail}</div>}
      </div>
    )
  }

  const { label, detail } = parseTicker(ticker)
  const display = ticker && label === ticker && fallbackTitle ? fallbackTitle : label
  return (
    <div>
      <div style={{ fontWeight: 500, fontSize: '0.8125rem' }}>{display}</div>
      {detail && <div className="muted" style={{ marginTop: 2 }}>{detail}</div>}
    </div>
  )
}
