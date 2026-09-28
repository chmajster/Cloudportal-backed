"""Redaction and outbound-network controls for Event Broker."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

from app.config import settings


SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(password|passwd|secret|token|authorization|api[_-]?key|private[_-]?key|client[_-]?secret|cookie)(?:$|[_-])",
    re.I,
)
JWT_LIKE = re.compile(r"^[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
AUTH_LIKE = re.compile(r"^(?:Bearer|Basic)\s+[A-Za-z0-9+/=_\-.]{8,}$", re.I)
PEM_MARKER = re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|SECRET)[A-Z0-9 ]*-----")
MAX_REDACT_DEPTH = 32

DEFAULT_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
}
DEFAULT_BLOCKED_CIDRS = (
    "0.0.0.0/8",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "::/128",
    "::1/128",
    "fe80::/10",
    "ff00::/8",
)


def _secret_value(value) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(PEM_MARKER.search(text) or AUTH_LIKE.fullmatch(text) or JWT_LIKE.fullmatch(text))


def redact(value, *, _depth=0):
    if _depth > MAX_REDACT_DEPTH:
        return "[REDACTED]"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key)
            result[name] = "[REDACTED]" if SENSITIVE_KEY.search(name) else redact(item, _depth=_depth + 1)
        return result
    if isinstance(value, list):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, tuple):
        return [redact(item, _depth=_depth + 1) for item in value]
    return "[REDACTED]" if _secret_value(value) else value


def event_broker_network_policy(db) -> dict:
    from app.models import Setting

    configured = db.get(Setting, "event_broker")
    value = dict(configured.value or {}) if configured else {}
    return {
        "allow_private_networks": bool(value.get("allow_private_networks", False)),
        "allowed_domains": list(value.get("allowed_domains") or settings().webhook_allowed_hosts or []),
        "blocked_hosts": list(value.get("blocked_hosts") or sorted(DEFAULT_BLOCKED_HOSTS)),
        "blocked_cidrs": list(value.get("blocked_cidrs") or DEFAULT_BLOCKED_CIDRS),
        "max_chain_depth": max(1, min(64, int(value.get("max_chain_depth", 16)))),
        "response_body_limit": max(1024, min(1024 * 1024, int(value.get("response_body_limit", 65536)))),
        "events_retention_days": max(1, min(3650, int(value.get("events_retention_days", settings().retention_events_days)))),
        "deliveries_retention_days": max(1, min(3650, int(value.get("deliveries_retention_days", 90)))),
        "dlq_retention_days": max(1, min(3650, int(value.get("dlq_retention_days", 180)))),
    }


def _domain_allowed(host: str, allowed: list[str]) -> bool:
    host = host.rstrip(".").lower()
    for candidate in allowed:
        candidate = str(candidate).strip().rstrip(".").lower()
        if not candidate:
            continue
        if candidate.startswith("*."):
            suffix = candidate[1:]
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == candidate:
            return True
    return False


def _blocked_ip(address: ipaddress._BaseAddress, policy: dict) -> bool:
    for cidr in policy["blocked_cidrs"]:
        try:
            if address in ipaddress.ip_network(str(cidr), strict=False):
                return True
        except ValueError:
            # Invalid administrator configuration fails closed.
            return True
    if policy["allow_private_networks"]:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def resolve_webhook_target(db, url: str) -> dict:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("Webhook URL must use HTTP or HTTPS")
    if parsed.scheme == "http" and not settings().allow_http:
        raise ValueError("HTTP webhook URLs are disabled")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Webhook URL is invalid")
    host = parsed.hostname.rstrip(".").lower()
    policy = event_broker_network_policy(db)
    if host in {str(value).rstrip(".").lower() for value in policy["blocked_hosts"]}:
        raise ValueError("Webhook host is blocked")
    if not policy["allowed_domains"] or not _domain_allowed(host, policy["allowed_domains"]):
        raise ValueError("Webhook host is not in the Event Broker allowlist")

    try:
        answers = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError("Webhook host could not be resolved") from None
    addresses = []
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
        if _blocked_ip(address, policy):
            raise ValueError("Webhook resolved to a blocked network")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError("Webhook host resolved to no usable address")
    return {"parsed": parsed, "host": host, "addresses": addresses, "policy": policy}


def pinned_url(parsed, address) -> str:
    host = str(address)
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    netloc = host + (f":{port}" if port else "")
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))
