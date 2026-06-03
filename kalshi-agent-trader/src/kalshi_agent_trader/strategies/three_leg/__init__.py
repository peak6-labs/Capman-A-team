"""Three-leg fatigue-hedge strategy: back a QF favourite (match + title) and
hedge the length of their win, sized harder when the QF→SF turnaround is short.

  cli `three-leg` → runner.run → screen.build_plans (+ length.discover, compute) → render
"""

from .screen import ThreeLegParams

__all__ = ["ThreeLegParams"]
