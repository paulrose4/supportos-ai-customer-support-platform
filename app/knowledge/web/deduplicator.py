import re
from dataclasses import dataclass

from app.knowledge.web.models import StructuredWebDocument

_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class DeduplicationDecision:
    accepted: bool
    reason: str | None = None


class WebDocumentDeduplicator:
    def __init__(self, *, near_duplicate_threshold: float = 0.96) -> None:
        if not 0.5 <= near_duplicate_threshold <= 1.0:
            raise ValueError("near duplicate threshold must be between 0.5 and 1.0")
        self._near_duplicate_threshold = near_duplicate_threshold
        self._urls: set[str] = set()
        self._hashes: set[str] = set()
        self._fingerprints: list[frozenset[str]] = []

    def consider(self, document: StructuredWebDocument) -> DeduplicationDecision:
        if document.canonical_url in self._urls:
            return DeduplicationDecision(False, "duplicate_canonical_url")
        if document.content_hash in self._hashes:
            return DeduplicationDecision(False, "duplicate_content")
        fingerprint = _fingerprint(document.body)
        for existing in self._fingerprints:
            if _containment_similarity(fingerprint, existing) >= self._near_duplicate_threshold:
                return DeduplicationDecision(False, "near_duplicate_content")
        self._urls.add(document.canonical_url)
        self._hashes.add(document.content_hash)
        if fingerprint:
            self._fingerprints.append(fingerprint)
        return DeduplicationDecision(True)


def _fingerprint(text: str) -> frozenset[str]:
    tokens = [token.casefold() for token in _TOKEN_PATTERN.findall(text)]
    if len(tokens) < 5:
        return frozenset(tokens)
    return frozenset(" ".join(tokens[index : index + 5]) for index in range(len(tokens) - 4))


def _containment_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    return max(intersection / len(left), intersection / len(right))
