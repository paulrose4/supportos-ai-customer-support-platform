from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalCaseGrade:
    case_id: str
    expected_document_ids: tuple[str, ...]
    retrieved_document_ids: tuple[str, ...]
    relevant_retrieved: int

    @property
    def recall(self) -> float:
        return self.relevant_retrieved / len(self.expected_document_ids)


@dataclass(frozen=True, slots=True)
class IdentifierRetrievalCaseGrade:
    case_id: str
    expected_identifiers: tuple[str, ...]
    retrieved_identifiers: tuple[str, ...]
    relevant_retrieved: int

    @property
    def recall(self) -> float:
        return self.relevant_retrieved / len(self.expected_identifiers)


def grade_retrieval_case(
    *,
    case_id: str,
    expected_document_ids: tuple[str, ...],
    retrieved_document_ids: tuple[str, ...],
    k: int = 10,
) -> RetrievalCaseGrade:
    if not expected_document_ids:
        raise ValueError("retrieval case requires expected_document_ids")
    if k < 1:
        raise ValueError("retrieval k must be positive")
    expected = set(expected_document_ids)
    top_k = tuple(dict.fromkeys(retrieved_document_ids))[:k]
    return RetrievalCaseGrade(
        case_id=case_id,
        expected_document_ids=expected_document_ids,
        retrieved_document_ids=top_k,
        relevant_retrieved=len(expected & set(top_k)),
    )


def recall_at_k(grades: tuple[RetrievalCaseGrade, ...]) -> float:
    expected = sum(len(grade.expected_document_ids) for grade in grades)
    if expected == 0:
        raise ValueError("retrieval evaluation requires relevant documents")
    return sum(grade.relevant_retrieved for grade in grades) / expected


def grade_identifier_retrieval_case(
    *,
    case_id: str,
    expected_identifiers: tuple[str, ...],
    retrieved_identifiers: tuple[str, ...],
    k: int = 10,
) -> IdentifierRetrievalCaseGrade:
    if not expected_identifiers:
        raise ValueError("identifier retrieval case requires expected identifiers")
    if k < 1:
        raise ValueError("retrieval k must be positive")
    expected = tuple(
        dict.fromkeys(
            _normalize_identifier(value) for value in expected_identifiers if value.strip()
        )
    )
    if not expected:
        raise ValueError("identifier retrieval case requires expected identifiers")
    top_k = tuple(
        dict.fromkeys(
            _normalize_identifier(value) for value in retrieved_identifiers if value.strip()
        )
    )[:k]
    return IdentifierRetrievalCaseGrade(
        case_id=case_id,
        expected_identifiers=expected,
        retrieved_identifiers=top_k,
        relevant_retrieved=len(set(expected) & set(top_k)),
    )


def identifier_recall_at_k(grades: tuple[IdentifierRetrievalCaseGrade, ...]) -> float:
    expected = sum(len(grade.expected_identifiers) for grade in grades)
    if expected == 0:
        raise ValueError("identifier retrieval evaluation requires relevant identifiers")
    return sum(grade.relevant_retrieved for grade in grades) / expected


def _normalize_identifier(value: str) -> str:
    return value.strip().upper()
