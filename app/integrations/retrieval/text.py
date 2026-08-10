import re
import unicodedata

_LATIN_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_CJK_SPAN = re.compile(r"[\u3400-\u9fff]+")


def normalize_search_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def tokenize_search_text(text: str) -> tuple[str, ...]:
    normalized = normalize_search_text(text)
    tokens = list(_LATIN_TOKEN.findall(normalized))
    for span in _CJK_SPAN.findall(normalized):
        if len(span) == 1:
            tokens.append(span)
        else:
            tokens.extend(span[index : index + 2] for index in range(len(span) - 1))
    return tuple(tokens)
