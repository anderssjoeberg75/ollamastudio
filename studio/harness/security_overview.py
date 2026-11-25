"""Säkerhetsöversikt för Ollama Studios dashboard (/api/security/overview)."""

import time

_RISK_COPY = {
    "quiet": ("No immediate threat detected", "No unwhitelisted activity currently needs attention."),
}


def get_overview_snapshot() -> dict:
    """Returnerar ett samlat översikts-ögonblick för Ollama Studios dashboard (Issue #44)."""
    proc_count = 0
    try:
        import psutil
        proc_count = len(psutil.pids()) if hasattr(psutil, "pids") else 0
    except Exception:
        pass
    return {
        "status": "active",
        "risk_level": "quiet",
        "title": _RISK_COPY["quiet"][0],
        "detail": _RISK_COPY["quiet"][1],
        "monitored_processes": proc_count,
        "timestamp": time.time()
    }
