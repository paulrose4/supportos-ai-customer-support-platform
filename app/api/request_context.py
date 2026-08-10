from collections.abc import Iterable
from hashlib import sha256
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from fastapi import Request

IPNetwork = IPv4Network | IPv6Network


def _trusted_networks(values: Iterable[str]) -> tuple[IPNetwork, ...]:
    networks: list[IPNetwork] = []
    for value in values:
        try:
            networks.append(ip_network(value.strip(), strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted_peer(peer: str, networks: tuple[IPNetwork, ...]) -> bool:
    try:
        address = ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in networks)


def _valid_ip(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return str(ip_address(candidate))
    except ValueError:
        return None


def source_address(
    request: Request,
    *,
    trusted_proxy_cidrs: Iterable[str] = (),
    trusted_forwarded_address: str | None = None,
) -> str:
    peer = request.client.host if request.client is not None else "unknown"
    if trusted_forwarded_address:
        # This argument is used only after a connector/site credential has
        # authenticated the upstream adapter. Validate it before storing it.
        return _valid_ip(trusted_forwarded_address) or peer

    networks = _trusted_networks(trusted_proxy_cidrs)
    if not _is_trusted_peer(peer, networks):
        return peer

    # Walk X-Forwarded-For from the application-facing proxy toward the
    # original client. The first non-trusted address is the source.
    forwarded = request.headers.get("x-forwarded-for", "")
    for candidate in reversed(forwarded.split(",")):
        address = _valid_ip(candidate)
        if address is not None and not _is_trusted_peer(address, networks):
            return address
    for header in ("cf-connecting-ip", "x-real-ip"):
        address = _valid_ip(request.headers.get(header, ""))
        if address is not None:
            return address
    return peer


def source_fingerprint(request: Request, *, trusted_proxy_cidrs: Iterable[str] = ()) -> str:
    return sha256(
        source_address(request, trusted_proxy_cidrs=trusted_proxy_cidrs).encode("utf-8")
    ).hexdigest()


def country_code(request: Request, *, trusted_forwarded_country: str | None = None) -> str | None:
    value = trusted_forwarded_country or request.headers.get("cf-ipcountry")
    return value.strip().upper() if value else None


def visitor_user_agent(
    request: Request,
    *,
    trusted_forwarded_user_agent: str | None = None,
) -> str | None:
    value = trusted_forwarded_user_agent or request.headers.get("user-agent")
    return value.strip() if value else None
