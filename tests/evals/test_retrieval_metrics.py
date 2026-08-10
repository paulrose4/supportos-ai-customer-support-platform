from evals.graders.retrieval import (
    grade_identifier_retrieval_case,
    grade_retrieval_case,
    identifier_recall_at_k,
    recall_at_k,
)


def test_recall_at_ten_counts_expected_documents_in_top_ten() -> None:
    first = grade_retrieval_case(
        case_id="one",
        expected_document_ids=("doc-a", "doc-b"),
        retrieved_document_ids=("doc-a", "noise", "doc-b", "outside-top-ten"),
        k=3,
    )
    second = grade_retrieval_case(
        case_id="two",
        expected_document_ids=("doc-c",),
        retrieved_document_ids=("noise",),
        k=10,
    )

    assert first.recall == 1.0
    assert recall_at_k((first, second)) == 2 / 3


def test_identifier_recall_is_case_insensitive_and_entity_based() -> None:
    grade = grade_identifier_retrieval_case(
        case_id="sku",
        expected_identifiers=("SKU-100",),
        retrieved_identifiers=("sku-100", "OTHER"),
        k=10,
    )

    assert grade.recall == 1.0
    assert identifier_recall_at_k((grade,)) == 1.0


def test_identifier_recall_deduplicates_expected_aliases() -> None:
    grade = grade_identifier_retrieval_case(
        case_id="duplicate-sku",
        expected_identifiers=("SKU-100", " sku-100 "),
        retrieved_identifiers=("SKU-100",),
        k=10,
    )

    assert grade.expected_identifiers == ("SKU-100",)
    assert grade.recall == 1.0
    assert identifier_recall_at_k((grade,)) == 1.0
