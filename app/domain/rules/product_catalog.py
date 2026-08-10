from collections.abc import Mapping

from app.domain.models import ProductDataStatus, ProductSnapshot


def advance_missing_product_status(
    *,
    current_missing_count: int,
    confirmation_threshold: int = 2,
) -> tuple[ProductDataStatus, int]:
    if current_missing_count < 0:
        raise ValueError("current missing count cannot be negative")
    if confirmation_threshold < 2:
        raise ValueError("missing confirmation threshold must be at least two")
    next_count = current_missing_count + 1
    status = (
        ProductDataStatus.EXPIRED
        if next_count >= confirmation_threshold
        else ProductDataStatus.PENDING_REMOVAL
    )
    return status, next_count


def product_identity_conflicts(
    existing: ProductSnapshot,
    candidate: ProductSnapshot,
) -> tuple[str, ...]:
    """Return identity-critical fields that disagree within one staged snapshot."""
    conflicts: list[str] = []
    if _normalized_text(existing.product_key) != _normalized_text(candidate.product_key):
        conflicts.append("product_key")
    if _normalized_url(existing.canonical_url) != _normalized_url(candidate.canonical_url):
        conflicts.append("canonical_url")

    for field_name in ("sku", "mpn", "name", "brand", "material", "weight", "price", "currency"):
        left = _normalized_text(getattr(existing, field_name))
        right = _normalized_text(getattr(candidate, field_name))
        if left and right and left != right:
            conflicts.append(field_name)

    if _mapping_conflicts(existing.dimensions, candidate.dimensions):
        conflicts.append("dimensions")
    # A content hash describes the fetched representation, not product
    # identity. The same canonical URL may legitimately change between
    # attempts (including after a parser release), and the staging snapshot
    # should replace its facts rather than reject the retry as a new product.
    return tuple(dict.fromkeys(conflicts))


def _mapping_conflicts(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    left_values = {
        _normalized_text(key): _normalized_text(value)
        for key, value in left.items()
        if _normalized_text(key) and _normalized_text(value)
    }
    right_values = {
        _normalized_text(key): _normalized_text(value)
        for key, value in right.items()
        if _normalized_text(key) and _normalized_text(value)
    }
    shared_keys = left_values.keys() & right_values.keys()
    return any(left_values[key] != right_values[key] for key in shared_keys)


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _normalized_url(value: str) -> str:
    return value.strip().rstrip("/").casefold()
