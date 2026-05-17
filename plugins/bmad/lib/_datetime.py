"""Date/time helpers for the BMAD plugin.

Pure-functional — no I/O, no side effects.
Used by lib/templates.py and lib/status.py.
"""

from datetime import date, datetime, timezone


def _today_iso() -> str:
    """Return local-date ISO 8601 string (YYYY-MM-DD)."""
    return date.today().isoformat()


def _now_iso() -> str:
    """Return local-datetime ISO 8601 string (YYYY-MM-DDTHH:MM:SS)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
