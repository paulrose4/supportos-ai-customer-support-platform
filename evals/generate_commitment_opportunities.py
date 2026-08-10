import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evals" / "datasets" / "commitment_opportunities.jsonl"

PRODUCTS = (
    ("SKU-100", "269"),
    ("SKU-101", "279"),
    ("SKU-102", "269"),
    ("SKU-103", "299"),
    ("SKU-104", "329"),
    ("SKU-105", "339"),
    ("SKU-106", "359"),
    ("SKU-107", "309"),
    ("SKU-108", "289"),
    ("SKU-109", "319"),
)
LANGUAGES = ("en", "zh-CN", "de", "fr", "es")
REGIONS = ("US", "CA", "GB", "DE", "AU")
VARIANTS = (
    "please advise",
    "before I order",
    "for my planning",
    "at checkout",
    "for this model",
)


def build_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for index in range(1_000):
        sku, price = PRODUCTS[index % len(PRODUCTS)]
        language = LANGUAGES[(index // len(PRODUCTS)) % len(LANGUAGES)]
        region = REGIONS[(index // 50) % len(REGIONS)]
        intent_index = index % 3
        if intent_index == 0:
            case = _delivery_case(index, sku, language, region)
        elif intent_index == 1:
            case = _price_case(index, sku, price, language, region)
        else:
            case = _policy_case(index, sku, language, region)
        cases.append(case)
    return cases


def _delivery_case(index: int, sku: str, language: str, region: str) -> dict[str, object]:
    return {
        "id": f"delivery-{index:04d}",
        "intent": "delivery_estimate",
        "language": language,
        "question": (
            f"How long will {sku} take to arrive in {region}? {VARIANTS[index % len(VARIANTS)]}."
        ),
        "evidence": (
            f"{sku}: order processing takes 3-7 days and shipping takes 7-20 days. "
            f"The estimate applies to region {region}; delays may occur."
        ),
        "approved_numbers": ["10", "27"],
        "required_numbers": ["3", "7", "20"],
        "required_phrases": [],
    }


def _price_case(index: int, sku: str, price: str, language: str, region: str) -> dict[str, object]:
    return {
        "id": f"price-{index:04d}",
        "intent": "product_price",
        "language": language,
        "question": (
            f"What is the current listed price of {sku} for {region}? "
            f"{VARIANTS[index % len(VARIANTS)]}."
        ),
        "evidence": f"{sku} is listed at {price} USD. Region: {region}.",
        "approved_numbers": [],
        "required_numbers": [price],
        "required_phrases": [price],
    }


def _policy_case(index: int, sku: str, language: str, region: str) -> dict[str, object]:
    return {
        "id": f"policy-{index:04d}",
        "intent": "return_policy",
        "language": language,
        "question": (
            f"Can I return {sku} after delivery in {region}? {VARIANTS[index % len(VARIANTS)]}."
        ),
        "evidence": (
            f"For {sku} in region {region}, eligible unopened items may be requested for return "
            "within 30 days of delivery. "
            "Eligibility is subject to the published return conditions."
        ),
        "approved_numbers": [],
        "required_numbers": ["30"],
        "required_phrases": ["30"],
    }


def main() -> int:
    cases = build_cases()
    OUTPUT.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT), "case_count": len(cases)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
