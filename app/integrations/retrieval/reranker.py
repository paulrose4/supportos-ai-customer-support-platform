from collections.abc import Sequence
from dataclasses import replace

from app.domain.models import KnowledgeEvidence
from app.domain.ports import GLOBAL_KNOWLEDGE_PARTITION, KnowledgeQuery
from app.integrations.retrieval.text import normalize_search_text, tokenize_search_text


class DeterministicKnowledgeReranker:
    async def rerank(
        self,
        *,
        query: KnowledgeQuery,
        candidates: Sequence[KnowledgeEvidence],
        limit: int,
    ) -> list[KnowledgeEvidence]:
        ranked = [_score_candidate(query, candidate) for candidate in candidates]
        ranked = [candidate for candidate in ranked if candidate.score >= query.score_threshold]
        ranked.sort(key=_rank_key, reverse=True)
        return ranked[:limit]


def _score_candidate(query: KnowledgeQuery, candidate: KnowledgeEvidence) -> KnowledgeEvidence:
    query_tokens = set(tokenize_search_text(query.text))
    document_tokens = set(tokenize_search_text(candidate.text))
    lexical_score = len(query_tokens & document_tokens) / max(len(query_tokens), 1)
    query_identifiers = {token for token in query_tokens if _is_identifier(token)}
    if query_identifiers & document_tokens:
        lexical_score = max(lexical_score, 0.7)
    normalized_query = normalize_search_text(query.text)
    normalized_document = normalize_search_text(candidate.text)
    if normalized_query and normalized_query in normalized_document:
        lexical_score = min(1.0, lexical_score + 0.25)

    retrieval_score = max(0.0, min(1.0, candidate.score))
    authority_score = _normalized_metadata_score(candidate.metadata.get("authority_level"))
    priority_score = _normalized_metadata_score(candidate.metadata.get("priority"))
    is_tenant = candidate.metadata.get("partition_id") != GLOBAL_KNOWLEDGE_PARTITION
    scope_score = 1.0 if is_tenant else 0.5
    relevance_score = retrieval_score * 0.55 + lexical_score * 0.4
    metadata_boost = 0.0
    if retrieval_score >= 0.5 or lexical_score >= 0.15:
        metadata_boost = authority_score * 0.06 + priority_score * 0.02 + scope_score * 0.02
    final_score = min(1.0, relevance_score + metadata_boost)
    metadata = {
        **candidate.metadata,
        "retrieval_score": candidate.score,
        "lexical_score": lexical_score,
        "rerank_score": final_score,
    }
    return replace(candidate, score=final_score, metadata=metadata)


def _normalized_metadata_score(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    divisor = 10.0 if number <= 10 else 100.0
    return max(0.0, min(1.0, number / divisor))


def _is_identifier(token: str) -> bool:
    return (
        len(token) >= 3
        and any(character.isalpha() for character in token)
        and any(character.isdigit() for character in token)
    )


def _rank_key(candidate: KnowledgeEvidence) -> tuple[float, int, int]:
    return (
        candidate.score,
        int(candidate.metadata.get("authority_level", 0)),
        int(candidate.metadata.get("priority", 0)),
    )
