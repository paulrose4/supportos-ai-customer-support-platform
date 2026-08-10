from datetime import datetime

from app.application.dto.lead_scoring import LeadScoreResult, ScoreLeadCommand
from app.domain.models.presence import VisitorPresence
from app.domain.rules.lead_scoring import score_lead


class LeadScoringService:
    """Application boundary for the pure commercial-intent scorer."""

    def score(
        self,
        command_or_presence: ScoreLeadCommand | VisitorPresence,
        now: datetime | None = None,
        *,
        current_time: datetime | None = None,
        conversation_intent: object | None = None,
        page_taxonomy: object | None = None,
    ) -> LeadScoreResult:
        """Return a bounded score from a command or directly from presence.

        Commands are the preferred integration surface.  The direct presence
        form keeps the service convenient for a read-only queue projection and
        still requires no repository or provider dependency.
        """

        if isinstance(command_or_presence, ScoreLeadCommand):
            if (
                now is not None
                or current_time is not None
                or conversation_intent is not None
                or page_taxonomy is not None
            ):
                raise TypeError("command already contains scoring context")
            command = command_or_presence
        elif isinstance(command_or_presence, VisitorPresence):
            if now is not None and current_time is not None:
                raise TypeError("provide either now or current_time, not both")
            command = ScoreLeadCommand(
                presence=command_or_presence,
                now=current_time or now,
                conversation_intent=conversation_intent,
                page_taxonomy=page_taxonomy,
            )
        else:
            raise TypeError("score expects ScoreLeadCommand or VisitorPresence")

        return LeadScoreResult.from_domain(
            score_lead(
                command.presence,
                now=command.now,
                conversation_intent=command.conversation_intent,
                page_taxonomy=command.page_taxonomy,
            )
        )

    def evaluate(self, command: ScoreLeadCommand) -> LeadScoreResult:
        """Verb-oriented alias for adapters that expose an evaluate operation."""

        return self.score(command)

    def score_presence(
        self,
        presence: VisitorPresence,
        now: datetime | None = None,
        *,
        current_time: datetime | None = None,
        conversation_intent: object | None = None,
        page_taxonomy: object | None = None,
    ) -> LeadScoreResult:
        return self.score(
            presence,
            now,
            current_time=current_time,
            conversation_intent=conversation_intent,
            page_taxonomy=page_taxonomy,
        )

    score_lead = score
    calculate = score


CommercialIntentScoringService = LeadScoringService
LeadScoreService = LeadScoringService


__all__ = [
    "CommercialIntentScoringService",
    "LeadScoreService",
    "LeadScoringService",
]
