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

The structure (legs on DIFFERENT players): **Leg 1** = M wins the match · **Leg 2** = T (the
other player) wins the tournament · **Leg 3** = T wins the match in 5 sets (the OUT). The worry
case is T beating M but not winning the title — both directional legs die; Leg 3 pays only there.
Your job is to **pick the side** (who is M vs T) and confirm the legs.

1. **Pick the side (model-driven).** For the A-vs-B matchup, get the model's title read and
   both orientations:
   ```bash
   kalshi-trader simulate --json --gender men             # model P(title) per player vs market + Polymarket
   kalshi-trader three-leg --json --match-on favorite --player "<A>"
   kalshi-trader three-leg --json --match-on underdog --player "<A>"
   ```
   Back for the **TITLE** (Leg 2) whichever player the model flags as most **underpriced** to
   win the event (model P(title) > market, ideally corroborated by Polymarket); back the
   **OTHER** player in the **MATCH** (Leg 1). Choose `--match-on favorite` or `--match-on
   underdog` accordingly. ⚠️ The sim's title edge is **unvalidated** (it did not pass the
   convergence test) — treat it as a judgment input, not proven alpha; say so in the ticket.

2. **Get the real turnaround.** Default `rest_days` is 1. Web-search the *current* RG schedule
   for the match date and the title player's SF date; set `--rest-days` to the actual gap.

3. **Confirm the legs (each is a gate):**
   - [ ] **Match leg (M).** Edge over the de-vigged match `fair`? Edge 0 ⇒ Leg 1 sizes to 0,
         which is fine — don't fabricate an edge.
   - [ ] **Title leg (T).** Is the model's title edge real and the price defensible? **≤ 1
         title anchor per tournament** (titles are mutually exclusive).
   - [ ] **The out (Leg 3).** T wins in 5 — it pays ONLY in the worry case (T beats M, no
         title). It is a left-tail **out**, NOT alpha: judge it by how much it lifts the worst
         case, not by EV/edge. Confirm `hedge_pending: false` (else note it and the executor
         re-runs when the 5-set market lists).
   - [ ] **EV.** Read `ev_usd`. For a demo it may be negative — report it honestly; the value
         here is the payoff shape, not a proven edge.

4. **Decide and size.** Express your thesis via `--match-edge` / `--title-edge` and the chosen
   `--match-on`; re-run `--json` to capture the sized plan.

5. **Write the ticket.** Save to `research/tickets/<YYYY-MM-DD>-<player>.md` per
   `research/tickets/TEMPLATE.md`. Record **which player is M (match) vs T (title) and the
   `match_on` orientation**, the legs, EV-by-outcome, the edge claim (or why there isn't one),
   and copy the drift/staleness guards from `config.yaml` `three_leg:` into the frontmatter.

## Output
A short written verdict to the user — GO or NO-GO, the candidate, the one-line edge claim
(or "flat, as priors predicted"), and the ticket path. If GO, tell them they can hand the
ticket to the **executor** agent. Never place anything yourself.

## Canonical example (legs on DIFFERENT players)

**A = Alcaraz (match favourite), B = Sinner (opponent). Model says Sinner's TITLE is underpriced.**
Side chosen: back **Alcaraz to win the match** (Leg 1) + **Sinner to win the title** (Leg 2,
`--match-on favorite`), out = **Sinner wins in 5** (Leg 3).
```
Leg 1  ALCARAZ-MATCH-YES        ask $0.62  back A to win today
Leg 2  SINNER-TITLE-YES         ask $0.18  model P(title) 0.26 > market 0.18 (Poly 0.24) — the edge
Leg 3  SINNER-WINS-3-2-YES      ask $0.14  the OUT — pays iff Sinner beats Alcaraz in 5

Scenario shape:
  Alcaraz wins the match              → Leg 1 pays; B-title + out die
  Sinner wins, then wins the title    → Leg 2 pays big
  Sinner wins in ≤4, no title (worry) → all directional legs die, out doesn't fire
  Sinner wins in 5,  no title (worry) → the OUT fires, cushioning the grind

Verdict: GO — model + Polymarket both flag Sinner's title as underpriced; back him for the
title, Alcaraz for the match, Sinner-in-5 as the out. (Edge is a model read, NOT validated.)
```

**NO-GO** — neither orientation gives a defensible title edge (model ≈ market on both players),
and no match edge ⇒ legs size to 0. Write NO-GO: "flat, as priors predicted."