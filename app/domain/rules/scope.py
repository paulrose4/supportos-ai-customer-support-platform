import re

_OFFSITE_PATTERNS = (
    re.compile(
        r"(?i)\b(?:weather|forecast|election|politics|stock market|crypto price|bitcoin price)\b"
    ),
    re.compile(r"(?i)\b(?:write|debug|explain)\s+(?:python|javascript|java|c\+\+|code)\b"),
    re.compile(r"(?i)\b(?:homework|essay|cover letter|resume writing)\b"),
    re.compile(r"(?i)\b(?:capital of|who is the president|latest news)\b"),
    re.compile(r"(?:天气预报|天气怎么样|政治|选举|股票行情|比特币价格|最新新闻)"),
    re.compile(r"(?:写代码|调试代码|编程作业|家庭作业|写论文|写简历)"),
)

_PROTECTED_INTERNAL_PATTERNS = (
    re.compile(r"(?i)\b(?:raw\s+)?(?:system|developer)\s+(?:prompt|message|instructions?)\b"),
    re.compile(r"(?i)\b(?:tenant\s+id|raw\s+database|database\s+records?)\b"),
    re.compile(r"(?i)\b(?:retrieved|retrieval)\s+(?:internal\s+)?objects?\b"),
    re.compile(r"(?i)\b(?:private|hidden|internal)\s+reasoning\b|\bchain[ -]of[ -]thought\b"),
    re.compile(r"(?:系统提示词|开发者提示词|租户\s*ID|数据库记录|内部检索对象|私有推理|思维链)"),
)


def offsite_refusal(message: str, language: str) -> str | None:
    normalized = " ".join(message.split())
    if not any(pattern.search(normalized) for pattern in _OFFSITE_PATTERNS):
        return None
    if language.casefold().startswith("zh"):
        return "抱歉，我只处理本站商品导购、商品信息、FAQ和公开政策问题。这个问题不在服务范围内。"
    return (
        "Sorry, I can only help with this site's products, shopping guidance, FAQs, and "
        "published policies. That question is outside the support scope."
    )


def protected_internal_data_refusal(message: str, language: str) -> str | None:
    normalized = " ".join(message.split())
    if not any(pattern.search(normalized) for pattern in _PROTECTED_INTERNAL_PATTERNS):
        return None
    if language.casefold().startswith("zh"):
        return (
            "我不能提供系统或开发者提示词、租户标识、原始数据库或检索对象，也不能展示私有推理。"
            "我可以说明面向客户的结论，或列出可公开核实的商品与政策信息。"
        )
    return (
        "I can't provide system or developer prompts, tenant identifiers, raw database or "
        "retrieval objects, or private reasoning. I can explain the customer-facing conclusion "
        "or the published product and policy information instead."
    )
