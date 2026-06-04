import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import Control from './pages/Control'
import Portfolio from './pages/Portfolio'
import Trades from './pages/Trades'
import PnL from './pages/PnL'
import { getPortfolio, type PortfolioResponse } from './api'
import './index.css'

type Theme = 'dark' | 'light'

function getInitialTheme(): Theme {
  const saved = window.localStorage.getItem('theme')
  if (saved === 'dark' || saved === 'light') return saved
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function NavAccountValues() {
  const [data, setData] = useState<PortfolioResponse | null>(null)

  useEffect(() => {
    const load = () => getPortfolio().then(setData).catch(() => {})
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [])

  const fmt = (v: string | null | undefined) => {
    if (!v) return '—'
    const n = parseFloat(v)
    return isNaN(n) ? '—' : `$${n.toFixed(2)}`
  }

  if (!data) return null

  return (
    <div className="nav-right">
      <div className="nav-account">
        <span className="nav-amount">{fmt(data.cash_balance_usd)}</span>
        <span className="nav-sublabel">Cash</span>
      </div>
      <div className="nav-account">
        <span className="nav-amount nav-amount-pos">{fmt(data.portfolio_value_usd)}</span>
        <span className="nav-sublabel">Portfolio</span>
      </div>
    </div>
  )
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem('theme', theme)
  }, [theme])

  const nextTheme = theme === 'light' ? 'dark' : 'light'

  return (
    <button
      className="theme-toggle"
      type="button"
      aria-label={`Switch to ${nextTheme} mode`}
      title={`Switch to ${nextTheme} mode`}
      onClick={() => setTheme(nextTheme)}
    >
      <span className={`theme-toggle-icon ${theme}`} aria-hidden="true" />
    </button>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <nav>
        <span className="brand"><span className="brand-kalshi">Kalshi</span><span className="brand-sub">Trader Dashboard</span></span>
        <NavLink to="/" end>HOME</NavLink>
        <NavLink to="/portfolio">PORTFOLIO</NavLink>
        <NavLink to="/trades">TRADES</NavLink>
        <NavLink to="/pnl">PNL</NavLink>
        <div className="nav-actions">
          <NavAccountValues />
          <ThemeToggle />
        </div>
      </nav>
      <Routes>
        <Route path="/" element={<Control />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/trades" element={<Trades />} />
        <Route path="/pnl" element={<PnL />} />
      </Routes>
    </BrowserRouter>
  )
}
