import re
from collections import defaultdict
from pathlib import PurePosixPath
from urllib.parse import urlsplit

_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.IGNORECASE)


def normalize_language_tag(value: str) -> str:
    normalized = value.strip().replace("_", "-").casefold()
    if not _LANGUAGE_TAG.fullmatch(normalized):
        raise ValueError("invalid language tag")
    return normalized


def primary_language_code(value: str) -> str:
    return normalize_language_tag(value).split("-", 1)[0]


def language_matches_primary(value: str | None, primary_language: str) -> bool:
    if value is None or not value.strip():
        return True
    try:
        actual = primary_language_code(value)
        expected = primary_language_code(primary_language)
    except ValueError:
        return False
    return actual == expected


def first_path_segment(value: str) -> str | None:
    path = urlsplit(value).path
    return next((segment.casefold() for segment in path.split("/") if segment), None)


def is_translated_url(value: str, translated_locales: tuple[str, ...]) -> bool:
    segment = first_path_segment(value)
    if segment is None:
        return False
    normalized = {normalize_language_tag(locale) for locale in translated_locales if locale.strip()}
    return segment in normalized


def classify_sitemap_locales(
    urls: tuple[str, ...],
    *,
    primary_language: str,
) -> tuple[dict[str, str | None], tuple[str, ...]]:
    """Classify GTranslate-style sitemap suffixes using sibling sitemap evidence."""
    primary = normalize_language_tag(primary_language)
    names = {PurePosixPath(urlsplit(url).path).name.casefold() for url in urls}
    candidates: dict[str, tuple[tuple[str, str], ...]] = {}
    grouped: dict[str, set[str]] = defaultdict(set)
    for url in urls:
        name = PurePosixPath(urlsplit(url).path).name
        lowered = name.casefold()
        extension = ".xml.gz" if lowered.endswith(".xml.gz") else ".xml"
        if not lowered.endswith(extension):
            continue
        stem_parts = name[: -len(extension)].split("-")
        options: list[tuple[str, str]] = []
        for suffix_length in range(1, min(3, len(stem_parts) - 1) + 1):
            locale = "-".join(stem_parts[-suffix_length:]).casefold()
            try:
                locale = normalize_language_tag(locale)
            except ValueError:
                continue
            base_name = "-".join(stem_parts[:-suffix_length]) + extension
            options.append((locale, base_name.casefold()))
            grouped[base_name.casefold()].add(locale)
        if options:
            candidates[url] = tuple(options)

    result: dict[str, str | None] = {}
    detected: set[str] = set()
    for url in urls:
        options = candidates.get(url, ())
        candidate = next(
            (
                option
                for option in reversed(options)
                if option[1] in names or len(grouped[option[1]]) >= 2
            ),
            None,
        )
        if candidate is None:
            result[url] = None
            continue
        locale, base_name = candidate
        result[url] = locale
        if primary_language_code(locale) != primary_language_code(primary):
            detected.add(locale)
    return result, tuple(sorted(detected))
