from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SkeletonGrade:
    risk_correct: bool
    kind_correct: bool


def grade(
    *, expected_risk: int, expected_kind: str, actual_risk: int, actual_kind: str
) -> SkeletonGrade:
    return SkeletonGrade(
        risk_correct=expected_risk == actual_risk,
        kind_correct=expected_kind == actual_kind,
    )
