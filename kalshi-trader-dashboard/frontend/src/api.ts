const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

// Control
export const getControlStatus = () => get<ControlStatus>('/control/status')
export const setKillSwitch = (engaged: boolean) => post<{ kill_switch_engaged: boolean }>('/control/kill', { engaged })
export const setDryRun = (dry_run: boolean) => post<{ dry_run: boolean }>('/control/dry-run', { dry_run })

// Portfolio
export const getPortfolio = () => get<PortfolioResponse>('/portfolio')

// Trades
export const getCurrentTrades = () => get<CurrentTrades>('/trades/current')
export const getTradeHistory = (limit = 200) => get<TradeHistory>(`/trades/history?limit=${limit}`)

// PnL
export const getPnlSummary = () => get<PnlSummary>('/pnl/summary')
export const getPnlTimeseries = () => get<PnlTimeseries>('/pnl/timeseries')
export const getPnlCalibration = () => get<PnlCalibration>('/pnl/calibration')

// Types
export interface ControlStatus {
  kill_switch_engaged: boolean
  dry_run: boolean
  exchange: Record<string, unknown>
  auth: { credentials_present: boolean; auth_ok: boolean; error: string | null }
}

export interface Position {
  ticker: string
  position: string | null
  exposure_usd: string | null
  cost_usd: string | null
  realized_pnl_usd: string | null
  unrealized_pnl_usd: string | null
  raw: Record<string, unknown>
}

export interface Order {
  ticker: string
  action: string
  side: string
  price_usd: string | null
  count: string | null
  status: string | null
  raw: Record<string, unknown>
}

export interface PortfolioResponse {
  cash_balance_usd: string | null
  portfolio_value_usd: string | null
  open_positions: Position[]
  resting_orders: Order[]
}

export interface CurrentTrades {
  open_positions: Position[]
  resting_orders: Order[]
}

export interface Fill {
  ts: number | null
  ticker: string | null
  side: string | null
  action: string | null
  count: string | null
  price_usd: string | null
  fill_id: string | null
  raw: Record<string, unknown>
}

export interface Decision {
  ts: number
  source: string | null
  market_ticker: string | null
  side: string | null
  target_price: string | null
  fair_prob: number | null
  confidence: number | null
  outcome: string
  gate: string | null
  reason: string | null
}

export interface TradeHistory {
  fills: Fill[]
  decisions: Decision[]
}

export interface PnlSummary {
  realized_usd: string
  unrealized_usd: string
  total_usd: string
  as_of: number
}

export interface TimeseriesPoint {
  ts: number
  cumulative_realized_usd: string
}

export interface PnlTimeseries {
  points: TimeseriesPoint[]
  current_unrealized_usd: string
}

export interface CalibrationBucket {
  label: string
  count: number
  brier: number | null
  mean_predicted: number | null
  mean_realized: number | null
}

export interface PnlCalibration {
  scored: number
  skipped_unsettled: number
  skipped_no_prediction: number
  overall: CalibrationBucket
  by_source: CalibrationBucket[]
  by_category: CalibrationBucket[]
}
