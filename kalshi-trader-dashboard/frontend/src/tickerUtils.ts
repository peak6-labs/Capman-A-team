const CITY: Record<string, string> = {
  NYC: 'New York', LA: 'Los Angeles', LAX: 'Los Angeles',
  CHI: 'Chicago', ORD: 'Chicago', MIA: 'Miami',
  PHX: 'Phoenix', MSP: 'Minneapolis', MIN: 'Minneapolis',
  HOU: 'Houston', DAL: 'Dallas', DFW: 'Dallas',
  SEA: 'Seattle', DEN: 'Denver', BOS: 'Boston',
  ATL: 'Atlanta', SF: 'San Francisco', SFO: 'San Francisco',
  LV: 'Las Vegas', LAS: 'Las Vegas', PHL: 'Philadelphia',
  DC: 'Washington DC', DET: 'Detroit', CLE: 'Cleveland',
  CIN: 'Cincinnati', STL: 'St. Louis', MIL: 'Milwaukee',
  IND: 'Indianapolis', OKC: 'Oklahoma City',
  SA: 'San Antonio', SAS: 'San Antonio',
  NY: 'New York', GS: 'Golden State',
  POR: 'Portland', MEM: 'Memphis', NOP: 'New Orleans',
  UTA: 'Utah', ORL: 'Orlando', CHA: 'Charlotte',
  WAS: 'Washington', SLC: 'Salt Lake City', PIT: 'Pittsburgh',
}

const PREFIX: Record<string, string> = {
  // Weather
  HIGHTEMP: 'High Temp', LOWTEMP: 'Low Temp',
  // Basketball
  KXNBA: 'NBA', NBA: 'NBA',
  // Football
  KXNFL: 'NFL', NFL: 'NFL',
  // Baseball
  KXMLB: 'MLB', MLB: 'MLB',
  // Hockey
  KXNHL: 'NHL', NHL: 'NHL',
  // Tennis — ATP/WTA circuit
  KXATPMATCH: 'ATP Match', KXATPEXACTMATCH: 'ATP Exact Match',
  KXWTAMATCH: 'WTA Match', KXWTAEXACTMATCH: 'WTA Exact Match',
  ATPMATCH: 'ATP Match', ATPEXACTMATCH: 'ATP Exact Match',
  // Grand Slams
  KXFOMEN: 'French Open Men\'s', KXFOWOMEN: 'French Open Women\'s',
  KXWIMMEN: 'Wimbledon Men\'s', KXWIMWOMEN: 'Wimbledon Women\'s',
  KXUSOMEN: 'US Open Men\'s', KXUSOWOMEN: 'US Open Women\'s',
  KXAOMEN: 'Australian Open Men\'s', KXAOWOMEN: 'Australian Open Women\'s',
  FOMEN: 'French Open Men\'s', FOWOMEN: 'French Open Women\'s',
  // Crypto
  KXBTC: 'Bitcoin', KXETH: 'Ethereum', KXSOL: 'Solana',
  BTC: 'Bitcoin', ETH: 'Ethereum', SOL: 'Solana',
  // Markets
  KXNASDAQ: 'Nasdaq', KXSPY: 'S&P 500', KXINX: 'S&P 500',
  INX: 'S&P 500', NASDAQ: 'Nasdaq',
  // Macro
  KXPRES: 'US Election', PRES: 'US Election',
  KXFED: 'Fed Rate', FED: 'Fed Rate',
  KXCPI: 'CPI', CPI: 'CPI',
  KXUNRATE: 'Unemployment', UNRATE: 'Unemployment',
}

const MONTH: Record<string, string> = {
  JAN: 'Jan', FEB: 'Feb', MAR: 'Mar', APR: 'Apr', MAY: 'May', JUN: 'Jun',
  JUL: 'Jul', AUG: 'Aug', SEP: 'Sep', OCT: 'Oct', NOV: 'Nov', DEC: 'Dec',
}

// Tries to parse YYMONDD from the START of a string.
// Returns { date, rest } where rest is whatever follows the date (may be empty).
// e.g. "26JUN03BEARN" → { date: "Jun 3, 2026", rest: "BEARN" }
// e.g. "26JUN03"      → { date: "Jun 3, 2026", rest: "" }
// Returns null if no date found at the start.
function extractDate(s: string): { date: string; rest: string } | null {
  const m = s.match(/^(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(.*)/i)
  if (!m) return null
  const date = `${MONTH[m[2].toUpperCase()]} ${parseInt(m[3])}, 20${m[1]}`
  return { date, rest: m[4] }
}

// Tries to parse a standalone year "26" or "2026" → "2026"
function extractYear(s: string): string | null {
  if (/^\d{2}$/.test(s)) return `20${s}`
  if (/^\d{4}$/.test(s)) return s
  return null
}

// Format a detail token: T85→85°, B45000→$45k, numeric → as-is, else title-case
function fmtToken(s: string): string {
  if (!s) return ''
  if (/^T\d+(\.\d+)?$/.test(s)) return `${s.slice(1)}°`
  if (/^B\d+(\.\d+)?$/.test(s)) {
    const n = parseFloat(s.slice(1))
    return `$${n >= 1000 ? n.toLocaleString() : n}`
  }
  // Pure number (e.g. "32" in ARN32 — leave as-is appended to prev token)
  if (/^\d+$/.test(s)) return s
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase()
}

export interface ParsedTicker {
  label: string   // e.g. "ATP Match · Jun 3, 2026"
  detail: string  // e.g. "Bearn vs Ber"
}

export function parseTicker(ticker: string | null | undefined): ParsedTicker {
  if (!ticker) return { label: '—', detail: '' }

  const parts = ticker.split('-')

  // ── 1. Identify prefix (resolve via PREFIX map) ──────────────────────────
  // Try longest prefix first (e.g. KXATPEXACTMATCH before KXATPMATCH)
  let prefixLen = 0
  let prefixLabel = parts[0]
  for (let n = Math.min(parts.length, 3); n >= 1; n--) {
    const key = parts.slice(0, n).join('-')
    if (PREFIX[key]) { prefixLabel = PREFIX[key]; prefixLen = n; break }
  }
  if (prefixLen === 0) prefixLen = 1  // consume at least the first segment

  const remaining = parts.slice(prefixLen)

  // ── 2. Extract date from remaining segments ───────────────────────────────
  let dateStr: string | null = null
  const afterDate: string[] = []

  for (let i = 0; i < remaining.length; i++) {
    const seg = remaining[i]

    if (dateStr === null) {
      // Try full date extraction from start of segment
      const parsed = extractDate(seg)
      if (parsed) {
        dateStr = parsed.date
        if (parsed.rest) afterDate.push(parsed.rest)
        continue
      }
      // Try year-only
      const year = extractYear(seg)
      if (year) {
        dateStr = year
        continue
      }
    }

    afterDate.push(seg)
  }

  // ── 3. Build label = prefix + date ───────────────────────────────────────
  const label = dateStr ? `${prefixLabel} · ${dateStr}` : prefixLabel

  // ── 4. Format remaining detail tokens (e.g. player names, temps) ─────────
  // Resolve city codes first; remaining tokens become "vs"-joined pairs or comma list
  const resolved = afterDate.map(t => CITY[t.toUpperCase()] ?? fmtToken(t)).filter(Boolean)

  // If exactly 2 tokens, show as "A vs B" (common for match markets)
  const detail = resolved.length === 2
    ? `${resolved[0]} vs ${resolved[1]}`
    : resolved.join(' · ')

  return { label, detail }
}
