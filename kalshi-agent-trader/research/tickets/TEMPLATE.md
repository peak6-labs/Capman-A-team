---
strategy: three-leg
player: <Full Name>
gender: <men | women>
created: <YYYY-MM-DDThh:mm:ssZ>          # UTC; executor refuses tickets older than ticket_max_age_min
ticket_max_age_min: 30                   # copied from config.yaml three_leg:
verdict: <GO | NO-GO>
rest_days: <n>                           # actual QF→SF turnaround from the schedule, not the default
drift_tolerance:
  match: 0.02                            # abort execution if the live match ask drifts past this
  title: 0.03                            # ...and likewise for the title ask
---

## Legs (PROPOSE)

Source: `kalshi-trader three-leg --json --player "<name>" --rest-days <n> --match-edge <m> --title-edge <t>`

| Leg   | Ticker            | Side | Ask  | Fair | Contracts | Cost  |
|-------|-------------------|------|------|------|-----------|-------|
| match | KX...             | yes  | 0.74 | 0.78 | N         | $X    |
| title | KX...             | yes  | 0.31 | 0.33 | M         | $Y    |
| hedge | KX...set1, ...set2| yes  | ...  | —    | ρ·M       | $Z    |   <!-- or: "pending — market not live; re-run when <event> lists" -->

## Edge claim & why
<The specific, defensible edge that beats the flat-EV prior — or, for a NO-GO, exactly
what was checked and why it stays flat. Cite numbers from the --json snapshot.>

## EV by terminal outcome
| Outcome              | Prob | Net (no title) | Net (wins title) |
|----------------------|------|----------------|------------------|
| loses QF             | ...  | ...            | —                |
| wins, no title       | ...  | ...            | ...              |
| wins title           | ...  | ...            | ...              |
| long win → hedge pays| ...  | ...            | ...              |

EV ≈ $... (at market-implied conditional title prob), total cost $...

## Verdict rationale
<Skeptical default. State GO/NO-GO and the one decisive reason. For a GO, name the edge
and confirm: ≤1 title anchor, hedge ≤ the title it protects, EV positive after fees.>
