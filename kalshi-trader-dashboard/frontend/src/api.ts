const BASE = '/api'
const DEFAULT_TIMEOUT_MS = 30_000

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const res = await fetch(`${BASE}${path}`, { ...init, signal: controller.signal })
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
    return res.json()
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`, { cause: error })
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

async function get<T>(path: string, timeoutMs?: number): Promise<T> {
  return request<T>(path, {}, timeoutMs)
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// Control
export const getControlStatus = () => get<ControlStatus>('/control/status')
export const setKillSwitch = (engaged: boolean) => post<{ kill_switch_engaged: boolean }>('/control/kill', { engaged })
export const setDryRun = (dry_run: boolean) => post<{ dry_run: boolean }>('/control/dry-run', { dry_run })
export const getSignals = (limit = 10, refresh = false) =>
  get<SignalsResponse>(`/control/signals?limit=${limit}&refresh=${refresh ? 'true' : 'false'}`, 60_000)

// Portfolio
export const getPortfolio = () => get<PortfolioResponse>('/portfolio')

// Trades
export const getCurrentTrades = () => get<CurrentTrades>('/trades/current')
export const getTradeHistory = (limit = 200) => get<TradeHistory>(`/trades/history?limit=${limit}`)

// Chat
export interface ChatImageAttachment {
  media_type: string  // e.g. 'image/jpeg'
  data: string        // base64-encoded, no data-URL prefix
}

export const sendChat = (
  message: string,
  agent: 'executor' | 'research',
  history: ChatMessage[],
  username: string,
  images: ChatImageAttachment[] = [],
) =>
  post<{ reply: string }>('/chat', {
    message,
    agent,
    history: history.map(({ role, content }) => ({ role, content })),
    username,
    images,
  })

// PnL
export const getPnlSummary = () => get<PnlSummary>('/pnl/summary')
export const getPnlTimeseries = () => get<PnlTimeseries>('/pnl/timeseries')
export const getPnlDaily = () => get<PnlDaily>('/pnl/daily')
export const getPnlTrades = () => get<PnlTrades>('/pnl/trades')
export const getPnlCalibration = () => get<PnlCalibration>('/pnl/calibration')

// Types
export interface ControlStatus {
  kill_switch_engaged: boolean
  dry_run: boolean
  exchange: ExchangeStatus
  auth: { credentials_present: boolean; auth_ok: boolean; error: string | null }
  username: string
}

export interface ExchangeStatus {
  exchange_active?: boolean
  trading_active?: boolean
  error?: string
}

export interface SignalCandidate {
  ticker: string
  title: string
  category: string | null
  side: 'yes' | 'no'
  price: string
  spread: string
  score: number
  hours_to_expiry: number
  volume_fp: number
}

export interface SignalsResponse {
  scanned_at: number
  total_scanned: number
  candidates: SignalCandidate[]
  scan_status?: 'ready' | 'running' | 'error'
  scan_error?: string | null
  scan_started_at?: number | null
  scan_next_retry_at?: number | null
}

export interface Position {
  ticker: string
  position: string | null
  exposure_usd: string | null
  cost_usd: string | null
  realized_pnl_usd: string | null
  unrealized_pnl_usd: string | null
  side?: string | null    // authoritative yes/no from Kalshi (derived from position sign)
  title?: string | null   // authoritative market title
  name?: string | null    // authoritative held-side contract/player name
}

export interface Order {
  ticker: string
  action: string | null
  side: string | null
  price_usd: string | null
  count: string | null
  status: string | null
  title?: string | null
  name?: string | null
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
  ts: number
  ticker: string
  side: string | null
  action: string | null
  count: string | null
  price_usd: string | null
  yes_price_usd?: string | null
  no_price_usd?: string | null
  fee_usd?: string | null
  is_taker?: boolean | null
  fill_id: string | null
  title?: string | null
  name?: string | null
}

export interface Decision {
  ts: number
  source: string | null
  market_ticker: string | null
  side: string | null
  target_price: string | null
  fair_prob: number | null
  confidence: number | null
  max_contracts: string | null
  outcome: string
  gate: string | null
  reason: string | null
  title?: string | null
  name?: string | null
}

export interface TradeHistory {
  fills: Fill[]
  decisions: Decision[]
}

export interface PnlSummary {
  realized_usd: string
  unrealized_usd: string
  total_usd: string
  starting_bankroll_usd?: string
  account_value_usd?: string
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

export interface DailyPnlPoint {
  date: string
  realized_usd: string
  cumulative_realized_usd: string
}

export interface PnlDaily {
  start_date: string
  points: DailyPnlPoint[]
}

export interface PnlTradeOrder {
  ts: number
  action: string | null
  side: string | null
  count: string | null
  price_usd: string | null
  fee_usd?: string | null
  fill_id: string | null
}

export interface PnlTrade {
  ticker: string
  side: string | null
  held_side: string | null
  title?: string | null
  name?: string | null
  group_title?: string | null
  settlement_result?: string | null
  opened_at: number
  closed_at: number | null
  last_order_at: number
  order_count: number
  entry_price_usd: string | null
  exit_price_usd: string | null
  entry_count: string
  exit_count: string
  open_count: string
  final_position: string
  settlement_payout_usd: string | null
  total_cost_usd: string
  total_payout_usd: string
  total_return_usd: string
  total_return_pct: string
  pnl_usd: string
  status: 'open' | 'closed' | 'settled'
  orders: PnlTradeOrder[]
}

export interface PnlTrades {
  trades: PnlTrade[]
}

export interface CalibrationBucket {
  label: string
  count: number
  brier: number | null
  mean_predicted: number | null
  mean_realized: number | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  imagePreviews?: string[]  // data URLs for display only — not sent to backend
}

export interface PnlCalibration {
  scored: number
  skipped_unsettled: number
  skipped_no_prediction: number
  overall: CalibrationBucket
  by_source: CalibrationBucket[]
  by_category: CalibrationBucket[]
}
