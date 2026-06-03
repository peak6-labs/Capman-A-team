---
name: three-leg-execute
description: >-
  Execute an approved three-leg trade ticket through compliance → risk → execution. Use
  when a GO ticket exists and the user wants to act on it. Validates freshness and price
  drift first, defaults to dry-run, and never places live without explicit confirmation.
---

# Three-leg trade execution

The playbook for turning an approved trade ticket into orders — faithfully, within the
gates, and dry-run-first. You execute exactly what the ticket says or you refuse; you do
not re-derive the thesis or improvise sizing.

## Pre-flight: validate the ticket (refuse, don't fix)
1. **Read the ticket** in `research/tickets/`. Abort with a clear message if:
   - `verdict` is not `GO`;
   - now − `created` exceeds the staleness bound (`ticket_max_age_min`); or
   - any required leg is missing.
2. **Freshness / drift check.** Take a fresh snapshot for that player, **reproducing the
   ticket's chosen side** (`--match-on` favorite/underdog from the ticket — get this right, or
   you'll diff against the wrong orientation):
   ```bash
   kalshi-trader three-leg --json --player "<name>" --match-on <ticket orientation> \
       --rest-days <ticket value> --match-edge <ticket> --title-edge <ticket>
   ```
   Compare the live `ask` of the match and title legs to the ticket. If
   `|live_ask − ticket_ask|` exceeds `drift_tolerance_match` / `drift_tolerance_title`,
   **abort** — the market moved past what the analyst signed off on; ask for a re-research.
3. **Hedge availability.** If the ticket's hedge was `pending`, confirm `hedge_pending` is
   now false. If still pending, you may execute Legs 1–2 and report the hedge as deferred —
   say so explicitly; do not place a naked directional duration bet.

## Execute (dry-run unless explicitly told otherwise)
- Confirm the mode out loud: read `config.yaml` `risk.dry_run`. If true, every order is a
  DRY-RUN — proceed and report. If false, **stop and get explicit human confirmation for
  this specific ticket** before placing; approval of a prior trade never carries over.
- Submit:
  ```bash
  kalshi-trader three-leg --player "<name>" --match-on <ticket orientation> --rest-days <n> \
      --match-edge <m> --title-edge <t> --execute
  ```
  (`--execute` routes each sized leg through compliance → risk → execution and honours
  `dry_run`.) Use per-leg `kalshi-trader order` only if you need finer control than the
  bundled run.

## Report
For each leg: status (PLACED / DRY-RUN / REJECTED), the gate that acted, price, count,
and any **risk-cap clamp** (the risk gate treats a hedge as added gross exposure and will
shrink it — report the clamp honestly, never route around it). Then summarise the
resulting position and any deferred hedge leg.

### Example
```
Leg 1 (match YES):    DRY-RUN  — 7 contracts @ $0.71 = $4.97
Leg 2 (title YES):    DRY-RUN  — 3 contracts @ $0.38 = $1.14
Leg 3 (length hedge): REJECTED — risk gate clamped to 0 (gross exposure cap)
Position: +7 SWIATEK-26-QF-YES, +3 SWIATEK-TITLE-YES | Hedge: pending (clamped)
```

## After a fill
If a live position later needs protecting or unwinding, use the **`find-hedge`** skill —
it compares every hedge against simply exiting. Do not improvise a hedge from here.