"""Atomic, comment-preserving toggles for kill switch and dry_run.

The kill switch is a plain file op (mirrors the CLI kill/unkill commands verbatim).
The dry_run toggle does a targeted line-level edit of config.yaml so all comments
and other sections are preserved; it never calls yaml.dump which would strip them.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from kalshi_agent_trader.config import load_config
from kalshi_agent_trader.journal import PROJECT_ROOT
from kalshi_agent_trader.risk import KILL_SWITCH_PATH

# Derived from the package's own PROJECT_ROOT (repo root), which is always correct.
_CONFIG_YAML = PROJECT_ROOT / "config.yaml"
_CONFIG_WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

def engage_kill_switch() -> None:
    KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    KILL_SWITCH_PATH.write_text("halt")


def clear_kill_switch() -> None:
    if KILL_SWITCH_PATH.exists():
        KILL_SWITCH_PATH.unlink()


def kill_switch_engaged() -> bool:
    return KILL_SWITCH_PATH.exists()


# ---------------------------------------------------------------------------
# dry_run toggle
# ---------------------------------------------------------------------------

def set_dry_run(value: bool) -> bool:
    """Toggle risk.dry_run in config.yaml preserving all comments.

    Finds the `risk:` section, then the first `dry_run:` line within it, and
    replaces only that line's boolean value. Writes atomically via a temp file
    + os.replace. Returns the value re-read from disk.
    """
    with _CONFIG_WRITE_LOCK:
        lines = _CONFIG_YAML.read_text().splitlines(keepends=True)
        in_risk_section = False
        replaced = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Detect the start of the risk: block (top-level key, no leading spaces).
            if re.match(r'^risk\s*:', line):
                in_risk_section = True
                continue
            # Any other top-level key ends the risk section.
            if in_risk_section and re.match(r'^\S', line) and not stripped.startswith('#'):
                in_risk_section = False
            if in_risk_section and re.match(r'^\s+dry_run\s*:', line):
                new_val = "true" if value else "false"
                lines[i] = re.sub(r'(dry_run\s*:\s*).*', r'\g<1>' + new_val, line.rstrip()) + "\n"
                replaced = True
                break

        if not replaced:
            raise RuntimeError("Could not find 'dry_run' under 'risk:' in config.yaml")

        tmp_path = _CONFIG_YAML.with_suffix(".yaml.tmp")
        tmp_path.write_text("".join(lines))
        os.replace(tmp_path, _CONFIG_YAML)

    # Re-read to confirm.
    return load_config().risk.dry_run
