from app.domain.models import AuthenticatedPrincipal, ResponseKind, RiskLevel


class SkeletonResponseService:
    def build_message(
        self,
        *,
        message: str,
        principal: AuthenticatedPrincipal,
        risk_level: RiskLevel,
    ) -> tuple[str, ResponseKind]:
        del message
        if risk_level >= RiskLevel.SEVERE:
            return "已为您转交人工处理，请留意后续通知。", ResponseKind.HANDOFF
        if risk_level >= RiskLevel.HIGH_IMPACT_REQUEST:
            return (
                "当前项目骨架不会执行退款、取消或其他高影响操作，已进入人工处理流程。",
                ResponseKind.HANDOFF,
            )
        if risk_level == RiskLevel.AUTHENTICATED_READ and principal.is_anonymous:
            return "该问题需要先完成可信身份验证。", ResponseKind.CLARIFICATION
        return (
            "客服智能体项目骨架已运行；知识检索和业务查询将在后续阶段接入。",
            ResponseKind.ANSWER,
        )
