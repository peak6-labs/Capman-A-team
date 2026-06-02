"""Fit the favourite-longshot de-bias alpha to settled Kalshi tennis matches.

Samples broadly across ALL rounds (early-round blowouts matter), gets each
match's pre-match price from candlesticks, and MLE-fits alpha.

Usage: python scripts/calibrate_alpha.py [N_per_series]
"""
import sys, os, random, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from kalshi_agent_trader.config import load_config
from kalshi_agent_trader.client import KalshiClient
from kalshi_agent_trader.calibration import fit_alpha, calibration_table

N = int(sys.argv[1]) if len(sys.argv) > 1 else 120

def epoch(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def f(x):
    try: return float(x)
    except: return None
def cprice(c):
    p=c.get("price")
    if isinstance(p,dict):
        for k in ("mean_dollars","close_dollars","open_dollars"):
            v=f(p.get(k))
            if v and 0<v<1: return v
    yb=c.get("yes_bid",{}); ya=c.get("yes_ask",{})
    b=f(yb.get("close_dollars")) if isinstance(yb,dict) else None
    a=f(ya.get("close_dollars")) if isinstance(ya,dict) else None
    if b is not None and a is not None and 0<=b<=1 and 0<=a<=1 and (a+b)>0: return (a+b)/2
    return None
def has_vol(c): return f(c.get("volume_fp")) and f(c["volume_fp"])>0

cfg=load_config()
cfg.secrets.kalshi_api_base="https://api.elections.kalshi.com/trade-api/v2"
cfg.runtime.max_requests_per_second=3.0
rng=random.Random(11)
samples=[]; by_gender={"W":[],"M":[]}
with KalshiClient(cfg, max_retries=8) as cl:
    for series,label in [("KXWTAMATCH","W"),("KXATPMATCH","M")]:
        mkts=[];cursor=None
        for _ in range(6):
            p={"series_ticker":series,"status":"settled","limit":200}
            if cursor:p["cursor"]=cursor
            d=cl.get("/markets",params=p,auth=False)
            mkts+=d.get("markets",[]);cursor=d.get("cursor") or None
            if not cursor:break
        mkts=[m for m in mkts if m.get("result") in ("yes","no") and m.get("close_time")]
        rng.shuffle(mkts)                      # sample across ALL rounds, not just recent
        picked=mkts[:N]; scored=0
        for m in picked:
            tk=m["ticker"];close=epoch(m["close_time"]);res=m["result"]
            try:
                d=cl.get(f"/series/{series}/markets/{tk}/candlesticks",auth=False,
                         params={"start_ts":close-48*3600,"end_ts":close,"period_interval":60})
            except Exception: continue
            cs=d.get("candlesticks",[])
            if not cs: continue
            anchor=close-12*3600
            pre=[c for c in cs if c.get("end_period_ts",0)<=anchor]
            yp=None
            for c in reversed(pre):
                if has_vol(c):
                    yp=cprice(c)
                    if yp is not None: break
            if yp is None:
                for c in reversed(pre or cs):
                    yp=cprice(c)
                    if yp is not None: break
            if yp is None or abs(yp-0.5)<1e-9: continue
            # favorite side
            fp = yp if yp>0.5 else 1-yp
            fav_won = (res=="yes") if yp>0.5 else (res=="no")
            samples.append((fp, 1 if fav_won else 0))
            by_gender[label].append((fp,1 if fav_won else 0))
            scored+=1
        print(f"{series}: scored {scored}", flush=True)

res=fit_alpha(samples)
print(f"\n=== MLE fit on {res.n} matches ===")
print(f"alpha* = {res.alpha:.3f}   (loglik {res.loglik:.1f} vs alpha=1 {res.loglik_at_1:.1f}; LR={res.lr_vs_null:.1f})")
verdict = "favorites (bias confirmed)" if res.alpha>1.03 else ("upsets" if res.alpha<0.97 else "~calibrated (no edge)")
print(f"verdict: {verdict}")
print("\nCalibration at alpha* (favorite-side price bucket):")
print(f"{'bucket':>10} {'n':>4} {'avgPx':>6} {'actual':>7} {'pred(a*)':>8}")
for lo,hi,n,avg,act,pred in calibration_table(samples,res.alpha):
    print(f"{int(lo*100):>3}-{int(hi*100):<3}c {n:>4} {avg*100:5.1f}c {act*100:6.1f}% {pred*100:7.1f}%")
for lab in ("W","M"):
    if by_gender[lab]:
        r=fit_alpha(by_gender[lab]); print(f"{lab}: alpha*={r.alpha:.3f} (n={r.n})")
