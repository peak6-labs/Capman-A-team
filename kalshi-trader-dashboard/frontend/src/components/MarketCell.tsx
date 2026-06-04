import { parseTicker } from '../tickerUtils'

interface MarketCellProps {
  ticker: string | null | undefined
  fallbackTitle?: string | null
}

export default function MarketCell({ ticker, fallbackTitle }: MarketCellProps) {
  const { label, detail } = parseTicker(ticker)
  const display = ticker && label === ticker && fallbackTitle ? fallbackTitle : label

  return (
    <div>
      <div style={{ fontWeight: 500, fontSize: '0.8125rem' }}>{display}</div>
      {detail && <div className="muted" style={{ marginTop: 2 }}>{detail}</div>}
    </div>
  )
}
