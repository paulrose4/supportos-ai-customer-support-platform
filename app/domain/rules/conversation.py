import re
import unicodedata
from urllib.parse import urlparse

from app.domain.models import ConversationTurn

_LOW_INFORMATION_INPUT = re.compile(r"^[\W_\d]+$", re.UNICODE)
_REFERENCE_URL = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_REFERENCE_IDENTIFIER = re.compile(
    r"(?i)^(?=[a-z0-9_-]{4,}$)(?=[a-z0-9_-]*[a-z])(?=[a-z0-9_-]*\d)[a-z0-9_-]+$"
)
_REFERENCE_ONLY_WORDS = frozenset(
    {
        "this",
        "this one",
        "this product",
        "here",
        "这个",
        "这个产品",
        "就是这个",
        "こちら",
        "これ",
        "dieses",
        "este",
        "ce produit",
    }
)
_JAPANESE_TEXT = re.compile(r"[\u3040-\u30ff]")
_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")
_KOREAN_TEXT = re.compile(r"[\uac00-\ud7af]")
_CYRILLIC_TEXT = re.compile(r"[\u0400-\u04ff]")
_ARABIC_TEXT = re.compile(r"[\u0600-\u06ff]")
_LATIN_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_LATIN_LANGUAGE_MARKERS = {
    "en": frozenset(
        {"a", "are", "can", "do", "how", "is", "price", "shipping", "the", "what", "where", "you"}
    ),
    "de": frozenset({"das", "der", "die", "ist", "kann", "nicht", "und", "was", "wie", "wo"}),
    "es": frozenset(
        {
            "como",
            "cuanto",
            "cuesta",
            "donde",
            "el",
            "envio",
            "esta",
            "la",
            "para",
            "precio",
            "puede",
            "que",
            "un",
        }
    ),
    "fr": frozenset(
        {
            "ce",
            "combien",
            "comment",
            "est",
            "le",
            "la",
            "livraison",
            "ou",
            "peut",
            "prix",
            "quel",
            "une",
        }
    ),
    "it": frozenset({"che", "come", "consegna", "dove", "il", "la", "puo", "quanto", "un", "una"}),
    "pt": frozenset({"como", "entrega", "esta", "onde", "o", "para", "pode", "qual", "que", "um"}),
}
_GREETINGS = {
    "hi": "Hello! What can I help you with today?",
    "hello": "Hello! What can I help you with today?",
    "hey": "Hi! What can I help you with today?",
    "你好": "你好！今天想了解商品、配送、退换货，还是其他问题？",
    "您好": "您好！今天想了解商品、配送、退换货，还是其他问题？",
    "こんにちは": "こんにちは。商品、配送、返品について、何をお手伝いしましょうか？",
}
_THANKS = {
    "thanks": "You're welcome. Is there anything else I can help with?",
    "thank you": "You're welcome. Is there anything else I can help with?",
    "谢谢": "不客气。还有什么可以帮您的吗？",
    "ありがとう": "どういたしまして。ほかにお手伝いできることはありますか？",
}
_AMBIGUOUS_INPUT_TEMPLATES = {
    "en": (
        "I'm not sure what {value!r} refers to. What would you like help with: "
        "product details, shipping, returns, or something else?"
    ),
    "zh": "我还不确定“{value}”指什么。您想了解商品信息、配送、退换货，还是其他问题？",
    "ja": "「{value}」が何を指すのか分かりません。商品、配送、返品のどれについて知りたいですか？",
    "ko": "'{value}'이 무엇을 의미하는지 잘 모르겠습니다. 상품, 배송, 반품 중 무엇을 도와드릴까요?",
    "es": (
        "No estoy seguro de qué significa {value!r}. ¿Desea consultar productos, "
        "envíos, devoluciones u otra cosa?"
    ),
    "fr": (
        "Je ne sais pas exactement ce que {value!r} signifie. Souhaitez-vous parler "
        "d'un produit, de la livraison, d'un retour ou d'autre chose ?"
    ),
    "de": (
        "Ich bin nicht sicher, was {value!r} bedeutet. Geht es um ein Produkt, "
        "Versand, Rückgabe oder etwas anderes?"
    ),
    "pt": (
        "Não tenho certeza do que {value!r} significa. Você quer saber sobre produtos, "
        "entrega, devoluções ou outro assunto?"
    ),
}


def clarification_for_ambiguous_input(
    message: str,
    history: tuple[ConversationTurn, ...] = (),
    preferred_language: str | None = None,
) -> str | None:
    normalized = unicodedata.normalize("NFKC", message).strip()
    if not normalized:
        return "Could you tell me what you would like help with?"
    conversational_reply = _GREETINGS.get(normalized.casefold()) or _THANKS.get(
        normalized.casefold()
    )
    if conversational_reply:
        return conversational_reply
    if len(normalized) <= 12 and _LOW_INFORMATION_INPUT.fullmatch(normalized):
        language = resolve_response_language(normalized, history, preferred_language).split("-", 1)[
            0
        ]
        template = _AMBIGUOUS_INPUT_TEMPLATES.get(language, _AMBIGUOUS_INPUT_TEMPLATES["en"])
        return template.format(value=normalized)
    return None


def resolve_reference_followup(
    message: str,
    history: tuple[ConversationTurn, ...] = (),
) -> str:
    normalized = unicodedata.normalize("NFKC", message).strip()
    if not _is_reference_only(normalized):
        return normalized
    previous_question = next(
        (
            turn.content.strip()
            for turn in reversed(history)
            if turn.role == "user"
            and turn.content.strip()
            and not _is_reference_only(turn.content.strip())
        ),
        None,
    )
    if previous_question is None:
        return normalized
    return f"{previous_question}\nPRODUCT_REFERENCE: {normalized}"


def contains_product_reference(message: str) -> bool:
    return bool(_REFERENCE_URL.search(message) or _product_identifiers(message))


def product_reference_urls(message: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(url.rstrip(".,，。)）") for url in _REFERENCE_URL.findall(message)))


def product_reference_identifiers(message: str) -> tuple[str, ...]:
    variants = (
        variant
        for value in _product_identifiers(message)
        for variant in (value, value.upper(), value.lower())
    )
    return tuple(dict.fromkeys(variants))


def extract_product_skus(message: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.upper() for value in _product_identifiers(message)))


def product_reference_paths(message: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(urlparse(url).path or "/" for url in product_reference_urls(message))
    )


def resolve_response_language(
    message: str,
    history: tuple[ConversationTurn, ...] = (),
    preferred_language: str | None = None,
) -> str:
    current_language = detect_message_language(message)
    if current_language is not None:
        return current_language
    for turn in reversed(history):
        if turn.role != "user":
            continue
        history_language = detect_message_language(turn.content)
        if history_language is not None:
            return history_language
    return normalize_language_tag(preferred_language)


def detect_message_language(message: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", message).strip().casefold()
    if not normalized or _LOW_INFORMATION_INPUT.fullmatch(normalized):
        return None
    if _JAPANESE_TEXT.search(normalized):
        return "ja"
    if _KOREAN_TEXT.search(normalized):
        return "ko"
    if _CHINESE_TEXT.search(normalized):
        return "zh"
    if _CYRILLIC_TEXT.search(normalized):
        return "ru"
    if _ARABIC_TEXT.search(normalized):
        return "ar"
    latin_folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(character)
    )
    words = set(_LATIN_WORD.findall(latin_folded))
    if not words:
        return None
    scores = {
        language: len(words & markers) for language, markers in _LATIN_LANGUAGE_MARKERS.items()
    }
    best_language, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score:
        return best_language
    if "¿" in normalized or "¡" in normalized:
        return "es"
    if any("a" <= character <= "z" for character in latin_folded):
        return "en"
    return None


def normalize_language_tag(language: str | None) -> str:
    normalized = (language or "").strip().replace("_", "-").casefold()
    return normalized or "en"


def _is_reference_only(message: str) -> bool:
    if not message:
        return False
    without_urls = _REFERENCE_URL.sub(" ", message)
    remainder = " ".join(without_urls.casefold().split()).strip(" .,:;!?，。；：！？()（）")
    if _REFERENCE_URL.search(message) and (not remainder or remainder in _REFERENCE_ONLY_WORDS):
        return True
    return bool(_REFERENCE_IDENTIFIER.fullmatch(remainder))


def _product_identifiers(message: str) -> tuple[str, ...]:
    candidates = re.findall(r"[A-Za-z0-9_-]{4,}", message)
    return tuple(
        value
        for value in candidates
        if any(character.isalpha() for character in value)
        and any(character.isdigit() for character in value)
        and (
            "-" in value
            or "_" in value
            or sum(character.isupper() for character in value) >= 2
            or re.fullmatch(r"[A-Z]\d{4,}", value) is not None
        )
    )
