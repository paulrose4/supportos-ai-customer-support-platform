from hashlib import sha256

from fastapi import Request

from app.api.request_context import source_address, source_fingerprint


def _request(
    *,
    peer: str,
    headers: list[tuple[bytes, bytes]],
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/auth/email/login",
            "headers": headers,
            "client": (peer, 443),
            "server": ("support.example.com", 443),
            "scheme": "https",
        }
    )


def test_forwarded_headers_are_ignored_from_untrusted_peer() -> None:
    request = _request(
        peer="203.0.113.10",
        headers=[(b"x-forwarded-for", b"198.51.100.10")],
    )

    assert source_address(request, trusted_proxy_cidrs=("10.0.0.0/8",)) == "203.0.113.10"


def test_forwarded_chain_uses_first_non_proxy_address() -> None:
    request = _request(
        peer="10.1.2.3",
        headers=[(b"x-forwarded-for", b"198.51.100.10, 10.2.3.4")],
    )

    assert source_address(request, trusted_proxy_cidrs=("10.0.0.0/8",)) == "198.51.100.10"


def test_trusted_proxy_falls_back_to_cloudflare_header() -> None:
    request = _request(
        peer="10.1.2.3",
        headers=[(b"cf-connecting-ip", b"2001:db8::10")],
    )

    assert source_address(request, trusted_proxy_cidrs=("10.0.0.0/8",)) == "2001:db8::10"


def test_source_fingerprint_is_stable_for_resolved_source() -> None:
    request = _request(
        peer="10.1.2.3",
        headers=[(b"x-forwarded-for", b"198.51.100.10")],
    )

    assert (
        source_fingerprint(request, trusted_proxy_cidrs=("10.0.0.0/8",))
        == sha256(b"198.51.100.10").hexdigest()
    )
