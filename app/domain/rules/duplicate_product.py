"""Deterministic product identity rules used by website synchronization."""

import unicodedata

PRODUCT_IDENTITY_NORMALIZATION_VERSION = "product-identity-v1"
PRODUCT_IDENTITY_NORMALIZED_KEY_MAX_LENGTH = 500

_PLACEHOLDERS = frozenset(
    {
        "",
        "0",
        "na",
        "n/a",
        "none",
        "null",
        "unknown",
        "default",
        "undefined",
        "-",
        "_",
    }
)


def normalize_product_identity(value: str | None) -> str | None:
    """Return a stable grouping key, or ``None`` for missing placeholder values."""

    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    if normalized in _PLACEHOLDERS:
        return None
    # Spaces are presentation noise, while hyphens and slashes are meaningful SKU syntax.
    compact = "".join(normalized.split())
    if compact in _PLACEHOLDERS or not any(char.isalnum() for char in compact):
        return None
    if len(compact) > PRODUCT_IDENTITY_NORMALIZED_KEY_MAX_LENGTH:
        raise ValueError(
            "normalized product identity exceeds "
            f"{PRODUCT_IDENTITY_NORMALIZED_KEY_MAX_LENGTH} characters"
        )
    return compact


def duplicate_product_policy_is_supported(policy: str) -> bool:
    return policy in {"first_wins", "block", "manual_review"}
