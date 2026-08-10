from dataclasses import dataclass

from app.domain.rules.duplicate_product import (
    PRODUCT_IDENTITY_NORMALIZATION_VERSION,
    normalize_product_identity,
)


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    raw_value: str
    normalized_key: str
    source: str
    normalization_version: str = PRODUCT_IDENTITY_NORMALIZATION_VERSION

    @classmethod
    def from_value(cls, value: str | None, *, source: str) -> "ProductIdentity | None":
        normalized = normalize_product_identity(value)
        if normalized is None:
            return None
        return cls(raw_value=str(value), normalized_key=normalized, source=source)
