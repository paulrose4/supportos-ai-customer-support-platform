import re
from ipaddress import ip_address

_COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")


def normalize_visitor_ip_address(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def normalize_visitor_country_code(value: str | None) -> str | None:
    normalized = (value or "").strip().upper()
    if not _COUNTRY_CODE_PATTERN.fullmatch(normalized) or normalized == "XX":
        return None
    return normalized
