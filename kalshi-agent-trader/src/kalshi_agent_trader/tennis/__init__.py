"""Shared tennis domain primitives used by every tennis strategy.

Strategies depend on this package (and on `core` plumbing); they must never
import one another. ``fees`` holds the Kalshi fee model; ``pairing`` holds the
match↔title competitor pairing and the fetched ``Universe``.
"""
