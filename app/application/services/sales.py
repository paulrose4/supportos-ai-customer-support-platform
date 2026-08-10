from app.domain.models import (
    AnswerPlan,
    AuthenticatedPrincipal,
    ConversationLanguageContext,
    ConversationTurn,
    ConversationWorkingMemory,
    SalesResponsePlan,
    SiteIdentityProfile,
)
from app.domain.rules.sales import (
    build_sales_response_plan,
    resolve_conversation_language_context,
    resolve_site_identity,
)


class SalesConversationService:
    def resolve_site_identity(self, principal: AuthenticatedPrincipal) -> SiteIdentityProfile:
        return resolve_site_identity(principal)

    def resolve_language_context(
        self,
        *,
        message: str,
        history: tuple[ConversationTurn, ...],
        preferred_language: str | None,
        previous_language: str | None,
        address_mode: str,
        channel: str = "widget",
    ) -> ConversationLanguageContext:
        return resolve_conversation_language_context(
            message=message,
            history=history,
            preferred_language=preferred_language,
            previous_language=previous_language,
            address_mode=address_mode,
            channel=channel,
        )

    def plan_response(
        self,
        *,
        message: str,
        answer_plan: AnswerPlan,
        memory: ConversationWorkingMemory,
        language_context: ConversationLanguageContext,
        site_identity: SiteIdentityProfile,
    ) -> SalesResponsePlan:
        return build_sales_response_plan(
            message=message,
            answer_plan=answer_plan,
            memory=memory,
            language_context=language_context,
            site_identity=site_identity,
        )
