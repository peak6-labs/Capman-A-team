"""
Analyze queue.json with rule-based heuristics. Emits thesis.json.

Edge: longshot bias — sub-10¢ contracts are systematically overpriced on prediction
markets (documented in the academic literature). We exploit this by selling cheap
tails. The discount factors below are conservative placeholders; calibrate them
against historical Kalshi resolution data before increasing position sizes.

Kelly sizing for SELLING YES at price X:
  b     = X / (1 - X)          — profit/loss ratio for the seller
  f*    = (p_no * b - p_yes) / b
  count = (bankroll * f*) / (1 - X)   — contracts, floor 1

Position hard cap: never more than MAX_RISK_PER_POSITION of bankroll at risk on
a single position. Fat left tail on tail-selling means sizing matters more than
entry logic.
"""
import json

QUEUE_FILE = "queue.json"
THESIS_FILE = "thesis.json"

BANKROLL = 100.0            # dollars available to risk
MAX_KELLY = 0.25            # quarter-Kelly cap
MAX_RISK_PER_POSITION = 0.01  # hard cap: 1% of bankroll at risk per position
MIN_CONFIDENCE = 0.60       # skip theses below this
MAX_THESES = 10             # cap simultaneous positions


def estimate_probability(entry):
    """
    Stub: estimate true P(event happens) and confidence.

    Uses conservative longshot-bias discounts. These factors are NOT calibrated
    against real Kalshi data — treat them as a starting point, not ground truth.
    Replace with empirically fitted values once you have resolution history.

    Discount rationale: prediction market literature finds cheap contracts
    overpriced by 10–40% depending on market category and liquidity. We use the
    low end (10–20%) to stay conservative until calibrated.

    Returns (p_yes_estimated, confidence).
    """
    price = entry["price"]
    hours = entry["hours"]
    volume = entry.get("volume_24h", 0)

    # Conservative discounts — do not increase without backtested justification
    if price <= 0.02:
        p_yes = price * 0.80   # assume 20% overpriced
    elif price <= 0.05:
        p_yes = price * 0.88
    else:
        p_yes = price * 0.93   # near 10¢ market is more efficient — small discount

    # High volume = more efficient pricing, shrink our assumed edge further
    if volume > 10:
        p_yes = min(p_yes * 1.05, price * 0.98)

    # Confidence: reflects how well longshot bias applies, not P(win)
    confidence = min(0.90, 1.0 - price * 4)   # 1¢→0.96 capped at 0.90, 10¢→0.60
    if hours < 8:
        confidence *= 0.85

    return round(p_yes, 4), round(confidence, 3)


def kelly_fraction(p_yes_est, market_price):
    """Kelly fraction for selling at market_price given our probability estimate."""
    p_no = 1.0 - p_yes_est
    b = market_price / (1.0 - market_price)
    f_star = (p_no * b - p_yes_est) / b
    if f_star <= 0:
        return 0.0
    return round(min(f_star, MAX_KELLY), 4)


def contract_count(kf, market_price):
    """Contracts to sell, hard-capped at MAX_RISK_PER_POSITION of bankroll.

    Capital at risk per contract = (1 - market_price), since that's the max
    payout if YES resolves. Two independent limits apply: Kelly fraction and the
    hard per-position cap. Whichever is tighter wins.
    """
    risk_per_contract = 1.0 - market_price
    if risk_per_contract <= 0:
        return 0

    kelly_capital = BANKROLL * kf
    capped_capital = BANKROLL * MAX_RISK_PER_POSITION

    capital = min(kelly_capital, capped_capital)
    return max(1, int(capital / risk_per_contract))


def analyze(queue):
    theses = []

    for entry in queue:
        p_yes, confidence = estimate_probability(entry)
        kf = kelly_fraction(p_yes, entry["price"])

        if kf <= 0 or confidence < MIN_CONFIDENCE:
            continue

        count = contract_count(kf, entry["price"])
        theses.append({
            **entry,
            "p_yes_estimated": p_yes,
            "confidence": confidence,
            "kelly_fraction": kf,
            "count": count,
            "action": "sell",
        })

    theses.sort(
        key=lambda x: (x["confidence"], x["kelly_fraction"], x["score"]),
        reverse=True,
    )
    theses = theses[:MAX_THESES]

    with open(THESIS_FILE, "w") as f:
        json.dump(theses, f, indent=2)

    print(f"Analyzed {len(queue)} candidates → {len(theses)} theses → {THESIS_FILE}")
    return theses


def run():
    try:
        with open(QUEUE_FILE) as f:
            queue = json.load(f)
    except FileNotFoundError:
        print(f"{QUEUE_FILE} not found — run scanner.py first")
        return []
    return analyze(queue)


if __name__ == "__main__":
    run()
