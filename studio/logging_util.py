"""Shared logging setup.

Lightweight wrapper around the stdlib logging module so the rest of the code
can log instead of silently swallowing errors or printing ad-hoc. The level is
read from OLLAMA_STUDIO_LOG_LEVEL (default INFO) and logs go to stderr, which
journalctl/systemd capture alongside the startup banner.
"""
import logging
import os
import sys

_configured = False


def get_logger(name="ollamastudio"):
    """Return a configured logger. Safe to call from anywhere, any number of times."""
    global _configured
    if not _configured:
        level_name = (os.environ.get("OLLAMA_STUDIO_LOG_LEVEL") or "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )
        _configured = True
    return logging.getLogger(name)
