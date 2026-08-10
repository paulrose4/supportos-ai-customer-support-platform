from dataclasses import dataclass

from app.domain.models import AuthenticatedPrincipal, BusinessEvidence, ToolExecution


@dataclass(frozen=True, slots=True)
class QueryBusinessDataCommand:
    principal: AuthenticatedPrincipal
    message: str
    trace_id: str
    language: str = "zh-CN"


@dataclass(frozen=True, slots=True)
class BusinessDataQueryResult:
    status: str
    message: str | None
    citations: tuple[str, ...]
    evidence: tuple[BusinessEvidence, ...]
    tool_executions: tuple[ToolExecution, ...]
    failed_tools: tuple[str, ...] = ()
