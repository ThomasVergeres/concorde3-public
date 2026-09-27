"""Locate operator-owned subscription credentials without embedding a host path."""
import os
from pathlib import Path


def subscription_auth_file():
    configured = os.environ.get("CONCORDE_SUBSCRIPTION_AUTH_FILE")
    if configured:
        return Path(configured).expanduser()
    codex_home = os.environ.get("CODEX_HOME")
    return Path(codex_home).expanduser() / "auth.json" if codex_home else Path.home() / ".codex" / "auth.json"
