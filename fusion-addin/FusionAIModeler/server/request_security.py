"""Pure request-validation helpers for the localhost MCP transport."""

from typing import Any, Optional
from urllib.parse import urlparse


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _hostname(value: str) -> Optional[str]:
    """Return a normalized hostname from a Host header or absolute URL."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if "://" not in candidate:
        candidate = f"//{candidate}"
    try:
        return urlparse(candidate).hostname
    except ValueError:
        return None


def is_loopback_host(value: str) -> bool:
    """Accept only explicit loopback hostnames used by local MCP clients."""
    return _hostname(value) in LOOPBACK_HOSTS


def is_allowed_origin(value: Optional[str]) -> bool:
    """Allow non-browser clients (no Origin) and loopback browser origins only."""
    return value is None or is_loopback_host(value)


def is_json_content_type(value: Optional[str]) -> bool:
    """Require JSON so a browser cannot use a CORS-safelisted content type."""
    if not isinstance(value, str):
        return False
    return value.split(";", 1)[0].strip().lower() == "application/json"


def is_json_rpc_request(value: Any) -> bool:
    """Accept only JSON-RPC 2.0 request objects, never arrays or scalar JSON."""
    return isinstance(value, dict) and value.get("jsonrpc") == "2.0"
