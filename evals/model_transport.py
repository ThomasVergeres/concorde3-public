"""Explicit experimental transport qualification; never an API fallback."""

MUSE_PROFILE = "muse-spark-1.3-contributor"
MUSE_MODEL = "muse-spark-1.3-contributor"


def transport_ok(checks):
    # Old immutable records and standalone oracle witnesses predate this field.
    return checks.get("declared_transport", checks.get("subscription_only", False))


def api_transport_qualified(acts, preflight):
    return bool(acts) and preflight.get("auth_basis") == "explicit_api" and \
        preflight.get("model") == MUSE_MODEL and preflight.get("protocol") == 2 and \
        preflight.get("continuation") is True and all(
            a.get("config", {}).get("harness") == "command" and
            a.get("config", {}).get("model") == MUSE_MODEL and
            a.get("usage", {}).get("basis") in ("api", "command", "unknown", None, "")
            for a in acts)
