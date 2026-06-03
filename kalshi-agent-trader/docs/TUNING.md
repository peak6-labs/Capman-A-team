# Tuning guide — three-leg strategy knobs

All values live in `config.yaml` under `three_leg:`. Change them there; never hardcode them
in prompts or tickets. The executor reads them at submission time; the research agent copies
`ticket_max_age_min` and the drift tolerances into each ticket's frontmatter so the validity
check is self-contained.

---

## Sizing knobs

### `kelly_fraction`
What fraction of full Kelly each leg sizes to.

| Default | Range | Effect of moving it |
|---------|-------|---------------------|
| `0.5` | 0.25 – 0.5 | Lower → smaller positions, shallower drawdown, slower bankroll growth. Never exceed 0.5 without a large validated edge sample (≥50 resolved trades with positive Sharpe). |

**When to change:** lower to 0.25 during initial calibration. Leave at 0.5 once the edge
sample is validated.

---

### `fatigue_coef`
Fraction of conditional title probability lost per extra set per rest day between QF and SF.

| Default | Range | Effect of moving it |
|---------|-------|---------------------|
| `0.20` | 0.10 – 0.30 | Higher → larger length hedge for draining wins. Should be calibrated against historical conditional title-win rates grouped by match length and QF→SF turnaround. |

**When to change:** only after validating against resolved RG data. The default 0.20 is a
conservative prior — under-hedging is safer than sizing a −EV insurance position too large.

---

### `rest_days`
Default QF→SF turnaround in days, used when the analyst doesn't supply `--rest-days`.

| Default | Range | Effect of moving it |
|---------|-------|---------------------|
| `1` | 1 – 2 | Men's SF typically falls 2 days after the QF; women's 1 day. Fewer rest days → higher fatigue estimate → larger hedge. |

**When to change:** don't rely on the default. The research skill requires a web-search
confirmation of the actual turnaround and passes it as `--rest-days`; this default only
applies if that lookup is skipped.

---

## Executor guards (copied into each ticket's frontmatter)

### `ticket_max_age_min`
Maximum age of a trade ticket before the executor refuses it.

| Default | Range | Effect of moving it |
|---------|-------|---------------------|
| `30` | 15 – 60 | Lower → fresher prices required; more spurious aborts in slow handoffs. Higher → stale prices may slip through. |

**When to change:** tighten to 15 min when markets are volatile (late scratches, rain
delays). Loosen only if the research → execution handoff routinely takes more than 30 min.

---

### `drift_tolerance_match`
Maximum allowable change in the match leg ask between ticket creation and execution.

| Default | Range | Effect of moving it |
|---------|-------|---------------------|
| `0.02` | 0.01 – 0.05 | At 2¢ normal spread fluctuations pass; at 1¢ expect spurious aborts on thin books; at 5¢ you may execute at materially worse prices than the analyst approved. |

**When to change:** tighten to 0.01 for liquid, stable markets. For thin markets or
wide-spread contracts 0.03 is safer.

---

### `drift_tolerance_title`
Maximum allowable change in the title leg ask between ticket creation and execution.

| Default | Range | Effect of moving it |
|---------|-------|---------------------|
| `0.03` | 0.02 – 0.06 | Title markets move more than match markets (longer horizon, news-sensitive). 3¢ is the practical minimum; wider is safer when there is a lag between research and execution. |

**When to change:** same logic as `drift_tolerance_match`, but give title an extra cent of
slack — title prices are inherently more volatile and a tighter tolerance produces more
spurious aborts than it prevents bad fills.
