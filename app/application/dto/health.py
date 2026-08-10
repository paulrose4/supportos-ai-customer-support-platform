from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    is_ready: bool
    failed_dependencies: tuple[str, ...] = ()
