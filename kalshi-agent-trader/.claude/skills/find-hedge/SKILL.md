---
name: find-hedge
description: Find the best hedge — or exit — for an open Kalshi position. Use when asked to hedge, de-risk, protect, or lay off an open position, or "what should I do about my X position." Compares every hedge to simply exiting, prices edge, and respects risk caps. Places nothing without explicit confirmation.
---

# Find a hedge (or decide to just exit)

A disciplined playbook for laying off an open Kalshi position. The recurring
lesson it encodes: **a "hedge" often just locks a worse loss than selling** — so
you always compare against the exit baseline before recommending anything.

## When to use
The user wants to hedge / de-risk / protect / lay off an open position, or asks
"what should I do about my <player/market> position." Also when a position is
moving against them and they want options.

## Steps

1. **Run the tool first.** `kalshi-trader hedge` (auto-detects a sole position) or
   `kalshi-trader hedge --ticker <TICKER>`. It prints the **exit baseline** (sell
   at bid) vs the **equivalent hedge** (the opponent leg in the same event), with
   edge, locked P&L, and a risk-cap-clamped passive suggestion. It places nothing.
   - The command handles 2-outcome **match** markets cleanly. For richer cases
     (multi-leg events, correlated cross-event hedges), do step 2 by hand.

2. **Enumerate equivalent + correlated instruments** (the edge often hides here):
   - **Equivalent identities** — logically the *same* event, so a full 1-for-1
     hedge. In a tournament: *win a QF ≡ reach the SF ≡ the opponent loses*. Price
     "position loses" via every route (opponent match YES, your reach-SF NO, etc.)
     and take the **cheapest** — the wide ADVANCE markets are often *worse*, not
     stale-cheap, so verify rather than assume.
   - **Correlated (partial)** — pays in the lose-scenario but not only then (a
     title NO when you're long the match). Report how much of the per-contract
     loss it offsets; it is **not** a clean lock. Only prefer it when it carries
     real model edge (it doubles as a fade), never as efficient cover.

3. **Always compare to EXIT.** Compute `count × (bid − avg_cost)`. If exiting
   realizes a better number than the hedge's locked P&L, **recommend exiting** —
   the tool flags this as "exit beats this." A pure hedge that's dominated by
   selling is not a hedge worth doing.

4. **Passive vs protective is a real trade-off.** A passive bid *below* market
   only fills if the position **recovers** (the hedge gets cheaper) — so it
   captures cheap insurance + edge exactly when you need it least, and **won't
   fill in a fast adverse move**. Say this explicitly. Less passive = more
   protection, less edge.

5. **Respect the risk caps, and name them.** The risk gate counts a hedge as
   *added gross exposure*, not net risk reduction — so an existing position near
   the exposure cap will clamp the hedge hard (we've seen it clamp to 1 contract).
   Report the clamp; don't pretend a full hedge is available when it isn't.

6. **Never auto-place.** Present the recommended order (ticker, side, price,
   size) and require explicit confirmation. Note: `config.yaml` may have
   `dry_run: false`, which makes `order` **live by default** — only run `order`
   with the user's go, and prefer `--post-only`-style passive limits when adding a
   leg rather than crossing the spread.

## Output
A compact comparison — **EXIT vs each HEDGE** (cost, edge, P&L-if-position-loses,
whether exit dominates) — then a one-line recommendation, then the suggested
passive order with its size clamped to the live caps and the "fills on recovery"
caveat. End by asking whether to place it.