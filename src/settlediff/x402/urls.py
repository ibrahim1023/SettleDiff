"""Resource URL policy for x402 live requests."""

from __future__ import annotations

from contextlib import suppress
from ipaddress import ip_address
from urllib.parse import urlsplit


def is_safe_x402_target(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError:
        return False
    if (
        host is None
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return False
    if parsed.scheme == "https":
        return True
    loopback = host.casefold() == "localhost"
    with suppress(ValueError):
        loopback = loopback or ip_address(host).is_loopback
    return parsed.scheme == "http" and loopback
