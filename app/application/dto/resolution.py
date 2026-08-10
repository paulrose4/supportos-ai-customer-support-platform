from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RunAutoResolutionCommand:
    evaluated_at: datetime
    execute: bool = False
    limit: int = 500


@dataclass(frozen=True, slots=True)
class AutoResolutionResult:
    evaluated_at: datetime
    due_count: int
    resolved_count: int
    executed: bool
