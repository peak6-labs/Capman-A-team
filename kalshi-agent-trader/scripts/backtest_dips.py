"""Backtest the intraday title-dip strategy on every completed French Open match.

Pairs each FO title market (KXFOMEN/KXFOWOMEN) with the player's settled match
markets (KXATPMATCH/KXWTAMATCH) in the FO window, pulls 1-min candlesticks for
both, and replays reversion.py's detection + sizing, simulating each dip to a
realistic exit at executable prices. Reports the empirical reversion rate (=what
p_revert should be), the loss-when-wrong (=a realistic stop_loss), and net P&L,
then sweeps the detection thresholds.

Usage: python scripts/backtest_dips.py [fo_start=2026-05-24] [window_h=10] [limit]

Candles are cached under data/dip_candles/ so re-runs and the sweep are instant.
"""
import sys, os, json, datetime as dt
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from kalshi_agent_trader.config import load_config
from kalshi_agent_trader.client import KalshiClient
from kalshi_agent_trader.market_data import MarketData
from kalshi_agent_trader.reversion import DipParams
from kalshi_agent_trader.dip_backtest import Bar, replay_episode, aggregate

FO_START = sys.argv[1] if len(sys.argv) > 1 else "2026-05-24"
WINDOW_H = int(sys.argv[2]) if len(sys.argv) > 2 else 10
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 10_000

TITLE = {"KXATPMATCH": "KXFOMEN", "KXWTAMATCH": "KXFOWOMEN"}
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "dip_candles")
os.makedirs(CACHE, exist_ok=True)


def epoch(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def dcl(x):
    try:
        v = D(str(x))
        return v if 0 < v < 1 else None
    except Exception:
        return None


def cquotes(c):
    yb = c.get("yes_bid", {}) or {}; ya = c.get("yes_ask", {}) or {}
    return dcl(yb.get("close_dollars")), dcl(ya.get("close_dollars"))


def fetch_candles(md, series, ticker, start, end):
    key = os.path.join(CACHE, f"{ticker}_{start}_{end}.json")
    if os.path.exists(key):
        return json.load(open(key))
    cs = md.get_candlesticks(series, ticker, start_ts=start, end_ts=end, period_interval=1)
    json.dump(cs, open(key, "w"))
    return cs


def main():
    cfg = load_config()
    cfg.secrets.kalshi_api_base = "https://api.elections.kalshi.com/trade-api/v2"
    cfg.runtime.max_requests_per_second = 3.0
    fo_start = epoch(FO_START + "T00:00:00+00:00")

    with KalshiClient(cfg, max_retries=8) as cl:
        md = MarketData(cl)

        # 1) FO title competitor -> (ticker, series, name)
        title_idx = {}
        for series in ("KXFOMEN", "KXFOWOMEN"):
            cur = None
            while True:
                p = {"series_ticker": series, "limit": 200}
                if cur:
                    p["cursor"] = cur
                d = cl.get("/markets", params=p)
                for m in d.get("markets", []):
                    cid = (m.get("custom_strike") or {}).get("tennis_competitor")
                    if cid:
                        title_idx[str(cid)] = (m["ticker"], series, m.get("yes_sub_title"))
                cur = d.get("cursor") or None
                if not cur:
                    break
        print(f"FO title players: {len(title_idx)}", flush=True)

        # 2) settled FO-window match markets paired to a title player
        episodes = []  # (match_series, match_ticker, cid, close_ts)
        for series in ("KXATPMATCH", "KXWTAMATCH"):
            cur = None; pages = 0
            while pages < 10:
                p = {"series_ticker": series, "status": "settled", "limit": 200}
                if cur:
                    p["cursor"] = cur
                d = cl.get("/markets", params=p); pages += 1
                for m in d.get("markets", []):
                    cid = str((m.get("custom_strike") or {}).get("tennis_competitor") or "")
                    ct = m.get("close_time")
                    if cid in title_idx and ct and epoch(ct) >= fo_start:
                        episodes.append((series, m["ticker"], cid, epoch(ct)))
                cur = d.get("cursor") or None
                if not cur:
                    break
        print(f"FO-window match sides: {len(episodes)}", flush=True)

        # 3) fetch candles + build aligned bars (favourites only; cache title fetch)
        built = []  # (name, bars, cutoff_ts)
        anchor_params = DipParams()
        cutoff_back = 4 * 3600
        for i, (mseries, mtk, cid, close) in enumerate(episodes[:LIMIT]):
            start = close - WINDOW_H * 3600
            try:
                mc = fetch_candles(md, mseries, mtk, start, close)
            except Exception:
                continue
            mq = {c["end_period_ts"]: cquotes(c) for c in mc if c.get("end_period_ts")}
            # cheap favourite check on pre-match match median before fetching title
            pre = [mq[t] for t in mq if t <= close - cutoff_back and mq[t][0] and mq[t][1]]
            if not pre:
                continue
            mids = sorted((b + a) / 2 for b, a in pre)
            if mids[len(mids) // 2] < anchor_params.min_match_anchor:
                continue  # underdog side -> skip, no title fetch
            ttk, tseries, name = title_idx[cid]
            try:
                tc = fetch_candles(md, tseries, ttk, start, close)
            except Exception:
                continue
            tq = {c["end_period_ts"]: cquotes(c) for c in tc if c.get("end_period_ts")}
            bars = [Bar(ts, mq[ts][0], mq[ts][1], tq[ts][0], tq[ts][1])
                    for ts in sorted(set(mq) & set(tq))]
            if len(bars) < 30:
                continue
            built.append((f"{name} {dt.datetime.utcfromtimestamp(close).strftime('%m-%d')}",
                          bars, close - cutoff_back))
            if (i + 1) % 25 == 0:
                print(f"  ...{i+1}/{len(episodes)} processed, {len(built)} favourite episodes",
                      flush=True)
        print(f"\nFavourite episodes with usable candles: {len(built)}\n", flush=True)

    base = DipParams()

    def run(params, maker):
        return [t for t in (replay_episode(b, n, params, c, maker=maker)
                            for n, b, c in built) if t]

    def report(label, params, maker):
        trades = run(params, maker)
        st = aggregate(trades)
        print("=" * 78)
        print(f"{label}  threshold={params.residual_threshold} stop_loss={params.stop_loss} "
              f"floor={params.recover_floor}  (${params.bankroll} acct, "
              f"{params.max_position_frac*100:.0f}% cap)")
        if not st:
            print("  No dips detected/filled."); return
        print(f"  dips traded: {st.n}   exits: revert={st.n_revert} stop_match={st.n_stop_match} "
              f"stop_loss={st.n_stop_loss} settle={st.n_settle}")
        print(f"  >>> reversion rate = {st.win_rate*100:.0f}%   "
              f"per-contract edge mean {st.mean_cents*100:+.1f}c "
              f"(win {st.avg_cents_win*100:+.1f}c / lose {st.avg_cents_loss*100:+.1f}c)")
        print(f"  realized $ P&L: total {st.total_dollar_pnl:+.0f}  mean/trade {st.mean_dollar_pnl:+.2f}")
        return trades

    # 4) TAKER vs MAKER on the live defaults — the decisive comparison
    report("TAKER (lift the ask)", base, maker=False)
    mk = report("MAKER (rest a bid)", base, maker=True)
    if mk:
        print("\n  sample MAKER dips:")
        print(f"    {'player':<22}{'entry':>6}{'fair':>6}{'phi':>5}{'exit':>6}{'reason':>11}{'$pnl':>8}")
        for t in sorted(mk, key=lambda t: t.dollar_pnl)[:3] + sorted(mk, key=lambda t: -t.dollar_pnl)[:3]:
            print(f"    {t.player:<22}{t.entry_ask*100:5.0f}c{t.fair_at_entry*100:5.0f}c"
                  f"{t.phi*100:4.0f}%{t.exit_px*100:5.0f}c{t.reason:>11}{t.dollar_pnl:>+8.2f}")

    # 5) MAKER sweep over detection thresholds (instant; cached bars)
    print("\n" + "=" * 78)
    print("MAKER SWEEP")
    print(f"  {'threshold':>9}{'stop_loss':>10}{'n':>5}{'revert%':>9}{'mean¢':>8}{'total$':>9}")
    for thr in (D("0.04"), D("0.05"), D("0.06"), D("0.08")):
        for sl in (D("0.06"), D("0.10")):
            p = DipParams(residual_threshold=thr, stop_loss=sl)
            s = aggregate(run(p, maker=True))
            if s:
                print(f"  {float(thr):>9.2f}{float(sl):>10.2f}{s.n:>5}{s.win_rate*100:>8.0f}%"
                      f"{s.mean_cents*100:>+8.1f}{s.total_dollar_pnl:>+9.0f}")


if __name__ == "__main__":
    main()
