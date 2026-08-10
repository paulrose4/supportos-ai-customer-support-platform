import pytest

from app.domain.models.product_identity import ProductIdentity
from app.domain.rules.duplicate_product import normalize_product_identity


def test_normalize_product_identity_is_case_and_unicode_stable() -> None:
    assert normalize_product_identity("  Ｄ０４０２１ ") == "d04021"
    assert normalize_product_identity("d-04021") == "d-04021"
    assert normalize_product_identity("D / 04021") == "d/04021"


def test_normalize_product_identity_rejects_placeholders() -> None:
    for value in (None, "", "N/A", "unknown", "default", "0", "---"):
        assert normalize_product_identity(value) is None


def test_normalize_product_identity_handles_casefold_and_unicode_whitespace() -> None:
    assert normalize_product_identity(" STRA\u00dfE\u2003/ 01 ") == "strasse/01"
    assert normalize_product_identity("Ｃ０８－Ｂ１０２３") == "c08-b1023"


def test_product_identity_freezes_raw_normalized_and_versioned_values() -> None:
    identity = ProductIdentity.from_value(" C08-B1023 ", source="sku")

    assert identity is not None
    assert identity.raw_value == " C08-B1023 "
    assert identity.normalized_key == "c08-b1023"
    assert identity.source == "sku"
    assert identity.normalization_version == "product-identity-v1"


def test_normalize_product_identity_rejects_values_beyond_persistence_limit() -> None:
    with pytest.raises(ValueError, match="exceeds 500 characters"):
        normalize_product_identity("A" * 501)
