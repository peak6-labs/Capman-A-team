"""Intraday title-dip mean-reversion strategy (strategy 2).

Buy a tournament favourite's title YES when it over-reacts to an early in-match
deficit; exit on reversion. Detector + sizing in detector.py, order intents in
orders.py, candlestick backtest in backtest.py, live loop in runner.py.
"""
