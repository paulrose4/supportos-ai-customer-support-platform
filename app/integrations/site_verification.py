import asyncio
import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse


class SiteVerificationProbe:
    async def resolve_dns_txt(self, *, base_url: str) -> list[str]:
        hostname = urlparse(base_url).hostname or ""
        record_name = f"_managed-support-verify.{hostname}"
        try:
            import dns.resolver  # type: ignore[import-not-found]
        except ImportError:
            return []
        try:
            answers = await asyncio.to_thread(dns.resolver.resolve, record_name, "TXT", lifetime=5)
        except Exception:
            return []
        values: list[str] = []
        for answer in answers:
            values.extend(
                chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
                for chunk in getattr(answer, "strings", [str(answer)])
            )
        return values

    async def fetch_script_proof(self, *, base_url: str) -> str:
        url = base_url.rstrip("/") + "/.well-known/managed-support-verification.txt"

        def fetch() -> str:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            if not _host_resolves_to_public_address(hostname):
                return ""
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "managed-support-site-verifier/1"},
                method="GET",
            )
            try:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    _NoRedirectHandler(),
                )
                with opener.open(request, timeout=5) as response:
                    if response.status != 200 or _origin(response.geturl()) != _origin(base_url):
                        return ""
                    return response.read(512).decode("utf-8").strip()
            except (urllib.error.URLError, OSError, TimeoutError, UnicodeDecodeError):
                return ""

        return await asyncio.to_thread(fetch)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return None


def _origin(value: str) -> str:
    parsed = urlparse(value)
    return f"{parsed.scheme.casefold()}://{(parsed.hostname or '').casefold()}"


def _host_resolves_to_public_address(hostname: str) -> bool:
    try:
        addresses = {
            str(info[4][0]) for info in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        }
    except OSError:
        return False
    if not addresses:
        return False
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True
