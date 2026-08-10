from dataclasses import dataclass
from datetime import datetime

from app.domain.models import VisitorPresence
from app.domain.models.lead_scoring import (
    LeadFreshness,
    LeadIntentTier,
    LeadNextAction,
    LeadOperationPriority,
    LeadPageTaxonomy,
    LeadScore,
)


@dataclass(frozen=True, slots=True)
class ScoreLeadCommand:
    """Application input for scoring trusted visitor presence."""

    presence: VisitorPresence
    now: datetime | None = None
    conversation_intent: str | None = None
    page_taxonomy: str | None = None

    @property
    def visitor_presence(self) -> VisitorPresence:
        """Long-form alias used by adapters that avoid the short field name."""

        return self.presence

    @property
    def current_time(self) -> datetime | None:
        return self.now

    @property
    def page_kind(self) -> str | None:
        return self.page_taxonomy


@dataclass(frozen=True, slots=True)
class LeadScoreResult:
    """Channel-neutral DTO returned by :class:`LeadScoringService`."""

    commercial_intent: int
    tier: LeadIntentTier
    operation_priority: LeadOperationPriority
    confidence: float
    next_action: LeadNextAction
    signals: tuple[str, ...]
    freshness: LeadFreshness
    rule_version: str
    scored_at: datetime | None = None
    current_page_dwell_seconds: int = 0
    session_active_dwell_seconds: int = 0
    session_age_seconds: int = 0
    page_kind: LeadPageTaxonomy | None = None
    data_coverage: tuple[str, ...] = ()

    @classmethod
    def from_domain(cls, score: LeadScore) -> "LeadScoreResult":
        return cls(
            commercial_intent=score.commercial_intent,
            tier=score.tier,
            operation_priority=score.operation_priority,
            confidence=score.confidence,
            next_action=score.next_action,
            signals=score.signals,
            freshness=score.freshness,
            rule_version=score.rule_version,
            scored_at=score.scored_at,
            current_page_dwell_seconds=score.current_page_dwell_seconds,
            session_active_dwell_seconds=score.session_active_dwell_seconds,
            session_age_seconds=score.session_age_seconds,
            page_kind=score.page_kind,
            data_coverage=score.data_coverage,
        )

    @property
    def score(self) -> int:
        """Compatibility alias for consumers that display ``score``."""

        return self.commercial_intent

    @property
    def intent_score(self) -> int:
        return self.commercial_intent

    @property
    def intent_tier(self) -> LeadIntentTier:
        return self.tier

    @property
    def lead_score(self) -> int:
        return self.commercial_intent

    @property
    def queue_eligible(self) -> bool:
        return self.domain_score.queue_eligible

    @property
    def confidence_grade(self) -> str:
        return self.domain_score.confidence_grade

    @property
    def domain_score(self) -> LeadScore:
        """Rebuild the domain value when an adapter needs the rich model."""

        return LeadScore(
            commercial_intent=self.commercial_intent,
            tier=self.tier,
            operation_priority=self.operation_priority,
            confidence=self.confidence,
            next_action=self.next_action,
            signals=self.signals,
            freshness=self.freshness,
            rule_version=self.rule_version,
            scored_at=self.scored_at,
            current_page_dwell_seconds=self.current_page_dwell_seconds,
            session_active_dwell_seconds=self.session_active_dwell_seconds,
            session_age_seconds=self.session_age_seconds,
            page_kind=self.page_kind,
            data_coverage=self.data_coverage,
        )

    def as_dict(self) -> dict[str, object]:
        """Serialize only bounded, public scoring fields for an API adapter."""

        return {
            "commercial_intent": self.commercial_intent,
            "tier": self.tier.value,
            "intent_tier": self.intent_tier.value,
            "operation_priority": self.operation_priority.value,
            "confidence": self.confidence,
            "confidence_grade": self.confidence_grade,
            "queue_eligible": self.queue_eligible,
            "next_action": self.next_action.value,
            "signals": list(self.signals),
            "freshness": self.freshness.value,
            "rule_version": self.rule_version,
            "scored_at": self.scored_at.isoformat() if self.scored_at else None,
            "current_page_dwell_seconds": self.current_page_dwell_seconds,
            "session_active_dwell_seconds": self.session_active_dwell_seconds,
            "session_age_seconds": self.session_age_seconds,
            "page_kind": getattr(self.page_kind, "value", self.page_kind),
            "data_coverage": list(self.data_coverage),
        }


# Verb-oriented aliases make the DTO discoverable without duplicating shapes.
LeadScoringCommand = ScoreLeadCommand
CalculateLeadScoreCommand = ScoreLeadCommand
ScoreLeadResult = LeadScoreResult
LeadScoringResult = LeadScoreResult


__all__ = [
    "CalculateLeadScoreCommand",
    "LeadScoreResult",
    "LeadScoringCommand",
    "LeadScoringResult",
    "ScoreLeadCommand",
    "ScoreLeadResult",
]
