from app.domain.ports import KnowledgeVersionSchemaInvariantError
from app.knowledge.web.job_worker import _failure_error_code, _is_retryable_failure


def test_knowledge_version_schema_invariant_is_terminal_and_stable() -> None:
    error = KnowledgeVersionSchemaInvariantError("schema remediation required")

    assert _failure_error_code(error) == "knowledge_version_schema_invariant_violation"
    assert _is_retryable_failure(error) is False
    assert error.manual_remediation is True
