import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import Control from './pages/Control'
import Portfolio from './pages/Portfolio'
import Trades from './pages/Trades'
import PnL from './pages/PnL'
import './index.css'

export default function App() {
  return (
    <BrowserRouter>
      <nav>
        <span className="brand">Kalshi Trader</span>
        <NavLink to="/" end>Control</NavLink>
        <NavLink to="/portfolio">Portfolio</NavLink>
        <NavLink to="/trades">Trades</NavLink>
        <NavLink to="/pnl">PnL</NavLink>
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
