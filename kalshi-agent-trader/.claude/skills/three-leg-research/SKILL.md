---
name: three-leg-research
description: >-
  Research the French Open three-leg fatigue-hedge trade and write a GO/NO-GO trade
  ticket. Use when screening QF favourites, re-confirming an edge, or refreshing tickets
  for the executor. Screen-only and dry-run-safe — proposes, never places.
---

# Three-leg fatigue-hedge research

The playbook for deciding whether to back a French Open QF favourite across three legs and
hand the executor a trade ticket. You **propose**; the gates dispose. The recurring lesson
this encodes: **this structure backtested ~flat-EV, so a GO is the exception and must be
earned with a named edge — NO-GO is the honest default.**

## The strategy in one paragraph
For a backed favourite F: **Leg 1** match YES (F wins the QF), **Leg 2** title YES (F wins
the tournament), **Leg 3** a length hedge that pays when F wins *but long* (men 3-1/3-2;
women 2-1, proxied by the opponent's set-winner legs). The hedge cushions the fatigue a
draining win imposes on the deep-run title leg. Legs 1–2 size to **zero at market** unless
you supply a directional edge (`--match-edge` / `--title-edge`); the hedge sizes as a
turnaround-weighted fraction ρ of the title position, so **no title ⇒ no hedge**.

## Steps

> **Context loading** — load only what you need, when you need it. Start with the single
> candidate snapshot; don't pre-load all markets or events. Use web search only after you
> have the player's name and QF date in hand.

1. **Snapshot the live book (read-only).**
   ```bash
   kalshi-trader three-leg --json                       # all QFs, both genders
   kalshi-trader three-leg --json --player "Sabalenka"  # one candidate
   ```
   Parse the JSON: per plan you get `legs` (ask, market_fair, fair, contracts, cost),
   `outcomes` (prob, net_if_no_title, net_if_wins_title), `total_cost_usd`, `ev_usd`,
   `hedge_pending`. This is your evidence — quote numbers from it, don't eyeball a table.

2. **Get the real turnaround.** The default `rest_days` is 1. Web-search the *current* RG
   schedule for this player's QF date and their SF date; set `--rest-days` to the actual
   gap. Fewer rest days ⇒ a larger, more justified hedge. (Historically: men's SF later
   than women's, so men often get a 2-day turnaround, women 1-day.)

3. **Run the edge-confirmation checklist.** Each is a gate; a GO must pass all that apply:
   - [ ] **Match leg.** Is the de-vigged match `fair` materially above the `ask`? If you're
         only matching the market mid (edge 0), Leg 1 sizes to 0 — that is fine and honest.
         Do **not** fabricate an edge to force a position.
   - [ ] **Title leg & favourite-longshot bias.** Buying a cheap longshot title (e.g. a
         sub-10¢ title) is the WRONG side of the bias and is −EV. A title leg is defensible
         only for a genuine favourite-priced contender you think the market *underrates*.
   - [ ] **One champion.** Title legs across players are mutually exclusive ⇒ **≤ 1 title
         anchor per tournament.** Pick the single best anchor; don't stack title YES across
         players.
   - [ ] **Hedge sanity.** ρ = clamp(fatigue_coef·extra_sets/rest_days, 0, 1). Confirm the
         hedge market is live (`hedge_pending: false`); if pending, the ticket notes it and
         the executor must re-run when it lists. Remember the hedge is −EV insurance — it
         must be small relative to the title it protects, never a standalone duration bet.
   - [ ] **EV.** Read `ev_usd` (computed at the market-implied conditional title prob). If
         it isn't positive *after fees* under disciplined ½-Kelly, the verdict is NO-GO.

4. **Decide and size.** If GO, choose the leg edges that express your thesis
   (`--match-edge`, `--title-edge`) and re-run `--json` to capture the sized plan. Keep
   ≤ 1 title anchor. If nothing clears, write a NO-GO ticket explaining what you checked.

5. **Write the ticket.** Save to `research/tickets/<YYYY-MM-DD>-<player>.md` using the
   template in `research/tickets/TEMPLATE.md`. Fill in the legs, EV-by-outcome, the edge
   claim (or why there isn't one), and copy the drift/staleness guards from `config.yaml`
   `three_leg:` (`ticket_max_age_min`, `drift_tolerance_match/title`) into the frontmatter.

## Output
A short written verdict to the user — GO or NO-GO, the candidate, the one-line edge claim
(or "flat, as priors predicted"), and the ticket path. If GO, tell them they can hand the
ticket to the **executor** agent. Never place anything yourself.

## Canonical examples

**GO — Swiatek, RG 2026 QF (1-day turnaround)**
```
Leg 1  SWIATEK-26-QF-YES   ask $0.71  fair $0.76  edge +0.05  7 contracts  cost $4.97
Leg 2  SWIATEK-TITLE-YES   ask $0.38  fair $0.44  edge +0.06  3 contracts  cost $1.14
Leg 3  OPPONENT-SET-YES    ask $0.29  ρ=0.20      hedge       2 contracts  cost $0.58

EV by outcome:
  wins straight (prob 0.61):  +$1.73
  wins long     (prob 0.14):  +$0.95
  loses         (prob 0.25):  −$6.69
ev_usd: +$0.38  (after 7% fees, half-Kelly)

Verdict: GO — market underprices Swiatek's title given confirmed 5¢ match edge and
1-day QF→SF turnaround. Ticket: research/tickets/2026-06-03-swiatek.md
```

**NO-GO — Zverev, RG 2026 QF**
```
Leg 1  ZVEREV-26-QF-YES    ask $0.62  fair $0.63  edge +0.01  0 contracts  (no edge)
Leg 2  ZVEREV-TITLE-YES    ask $0.08  fair $0.06  edge −0.02  0 contracts  (wrong side FSB)
Leg 3  (no title position → no hedge)

ev_usd: $0.00

Verdict: NO-GO — no match edge; title ask is on the wrong side of the favourite-longshot
bias (market overprices a sub-10¢ longshot). Flat, as priors predicted.
```