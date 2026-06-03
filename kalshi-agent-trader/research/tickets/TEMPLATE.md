---
strategy: three-leg
player: <screen anchor, the favourite's Full Name>     # the --player you pass the CLI
match_player: <Full Name>                # M — backed to WIN THE MATCH (Leg 1)
title_player: <Full Name>                # T — backed to WIN THE TITLE (Leg 2); the OTHER player
match_on: <favorite | underdog>          # which player anchors the match leg (executor passes this)
gender: men                              # men only for now (Bo5; "5 sets" undefined for women)
created: <YYYY-MM-DDThh:mm:ssZ>          # UTC; executor refuses tickets older than ticket_max_age_min
ticket_max_age_min: 30                   # copied from config.yaml three_leg:
verdict: <GO | NO-GO>
rest_days: <n>                           # actual match→SF turnaround from the schedule
drift_tolerance:
  match: 0.02                            # abort execution if the live match ask drifts past this
  title: 0.03                            # ...and likewise for the title ask
---

## Legs (PROPOSE) — legs are on DIFFERENT players

Source: `kalshi-trader three-leg --json --player "<name>" --match-on <orientation> --rest-days <n> --match-edge <m> --title-edge <t>`

| Leg          | Player | Ticker | Side | Ask  | Contracts | Cost |
|--------------|--------|--------|------|------|-----------|------|
| 1 match      | M      | KX...  | yes  | 0.62 | N         | $X   |
| 2 title      | T      | KX...  | yes  | 0.18 | M         | $Y   |
| 3 out (T 3-2)| T      | KX...  | yes  | 0.14 | ρ·ref     | $Z   |   <!-- or: "pending — 5-set market not live; re-run when <event> lists" -->

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
