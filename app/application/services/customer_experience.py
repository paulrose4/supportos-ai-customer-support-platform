import re
from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.application.dto.customer_experience import (
    ApplyAutomationCommand,
    AutomationTestResult,
    CreateKnowledgeGapCommand,
    CreateOfflineTicketCommand,
    CustomerExperienceResult,
    DeleteAutomationRuleCommand,
    GetExperienceSummaryQuery,
    GetWidgetConfigurationQuery,
    ListAutomationExecutionsQuery,
    ListAutomationRulesQuery,
    ListKnowledgeGapsQuery,
    PublishWidgetVersionCommand,
    ResolveKnowledgeGapCommand,
    RollbackWidgetVersionCommand,
    SaveAutomationRuleCommand,
    SaveWidgetDraftCommand,
    SubmitSatisfactionCommand,
    TestAutomationRuleCommand,
)
from app.domain.models.customer_experience import (
    AutomationActions,
    AutomationConditions,
    AutomationExecution,
    AutomationFacts,
    AutomationRule,
    KnowledgeGap,
    KnowledgeGapStatus,
    WidgetConfig,
)
from app.domain.models.principal import AuthenticatedPrincipal
from app.domain.models.widget_asset import WidgetAssetStatus
from app.domain.ports.customer_experience import CustomerExperiencePort
from app.domain.ports.widget_assets import WidgetAssetRepositoryPort
from app.domain.rules import redact_sensitive_text, require_scope

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_TIME_RANGE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d-(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$")
_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}
_ALLOWED_INTENTS = {"knowledge", "order", "ticket", "logistics", "refund", "other"}


class CustomerExperienceService:
    def __init__(
        self,
        port: CustomerExperiencePort,
        widget_assets: WidgetAssetRepositoryPort | None = None,
    ) -> None:
        self._port = port
        self._widget_assets = widget_assets

    async def get_widget_configuration(
        self, query: GetWidgetConfigurationQuery
    ) -> CustomerExperienceResult:
        require_scope(query.principal, "sites:manage")
        state = await self._port.get_widget_configuration(
            tenant_id=query.principal.tenant_id, site_id=_opaque(query.site_id, "site_id")
        )
        if state is None:
            raise LookupError("site was not found")
        return CustomerExperienceResult(widget_configuration=state)

    async def save_widget_draft(self, command: SaveWidgetDraftCommand) -> CustomerExperienceResult:
        require_scope(command.principal, "sites:manage")
        _idempotency(command.idempotency_key)
        config = validate_widget_config(command.config)
        site_id = _opaque(command.site_id, "site_id")
        await self._validate_widget_assets(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
            config=config,
        )
        state = await self._port.save_widget_draft(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
            version_id=str(uuid4()),
            config=config,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
            created_at=datetime.now(UTC),
        )
        return CustomerExperienceResult(widget_configuration=state)

    async def _validate_widget_assets(
        self, *, tenant_id: str, site_id: str, config: WidgetConfig
    ) -> None:
        asset_ids = tuple(
            dict.fromkeys(
                value for value in (config.launcher_asset_id, config.agent_avatar_asset_id) if value
            )
        )
        if not asset_ids:
            return
        if self._widget_assets is None:
            raise ValueError("managed widget images are unavailable")
        for asset_id in asset_ids:
            asset = await self._widget_assets.get_asset(
                tenant_id=tenant_id,
                site_id=site_id,
                asset_id=_opaque(asset_id, "widget asset id"),
            )
            if asset is None or asset.status is not WidgetAssetStatus.ACTIVE:
                raise ValueError("widget image does not belong to this site or is unavailable")

    async def publish_widget_version(
        self, command: PublishWidgetVersionCommand
    ) -> CustomerExperienceResult:
        require_scope(command.principal, "sites:manage")
        _idempotency(command.idempotency_key)
        state = await self._port.publish_widget_version(
            tenant_id=command.principal.tenant_id,
            site_id=_opaque(command.site_id, "site_id"),
            version_id=_opaque(command.version_id, "version_id"),
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
            published_at=datetime.now(UTC),
        )
        return CustomerExperienceResult(widget_configuration=state)

    async def rollback_widget_version(
        self, command: RollbackWidgetVersionCommand
    ) -> CustomerExperienceResult:
        require_scope(command.principal, "sites:manage")
        _idempotency(command.idempotency_key)
        state = await self._port.rollback_widget_version(
            tenant_id=command.principal.tenant_id,
            site_id=_opaque(command.site_id, "site_id"),
            source_version_id=_opaque(command.version_id, "version_id"),
            new_version_id=str(uuid4()),
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
            published_at=datetime.now(UTC),
        )
        return CustomerExperienceResult(widget_configuration=state)

    async def list_rules(self, query: ListAutomationRulesQuery) -> CustomerExperienceResult:
        require_scope(query.principal, "automation:read")
        rules = await self._port.list_automation_rules(tenant_id=query.principal.tenant_id)
        return CustomerExperienceResult(automation_rules=tuple(rules))

    async def save_rule(self, command: SaveAutomationRuleCommand) -> AutomationRule:
        require_scope(command.principal, "automation:manage")
        _idempotency(command.idempotency_key)
        now = datetime.now(UTC)
        rule_id = command.rule_id or f"rule_{uuid4().hex}"
        if not _RULE_ID.fullmatch(rule_id):
            raise ValueError("rule_id must be a bounded opaque identifier")
        rule = AutomationRule(
            rule_id=rule_id,
            tenant_id=command.principal.tenant_id,
            name=_text(command.name, "name", 120),
            enabled=command.enabled,
            sort_order=max(0, min(command.sort_order, 10_000)),
            conditions=validate_conditions(command.conditions),
            actions=validate_actions(command.actions),
            created_at=now,
            updated_at=now,
        )
        return await self._port.save_automation_rule(
            rule=rule,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
        )

    async def delete_rule(self, command: DeleteAutomationRuleCommand) -> bool:
        require_scope(command.principal, "automation:manage")
        _idempotency(command.idempotency_key)
        return await self._port.delete_automation_rule(
            tenant_id=command.principal.tenant_id,
            rule_id=_opaque(command.rule_id, "rule_id"),
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
            deleted_at=datetime.now(UTC),
        )

    def test_rule(self, command: TestAutomationRuleCommand) -> AutomationTestResult:
        require_scope(command.principal, "automation:manage")
        return _match(validate_conditions(command.conditions), command.facts)

    async def apply_automation(
        self, command: ApplyAutomationCommand
    ) -> tuple[AutomationExecution, ...]:
        site_id = command.principal.site_id
        if site_id is None:
            return ()
        now = datetime.now(UTC)
        facts = await self._port.get_automation_facts(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
            conversation_id=command.conversation_id,
            page_path=command.page_path,
            user_intent=command.user_intent,
            risk_level=command.risk_level,
            authenticated="anonymous" not in command.principal.roles,
            dwell_seconds=max(0, min(command.dwell_seconds, 86_400)),
            occurred_at=now,
        )
        rules = await self._port.list_automation_rules(tenant_id=command.principal.tenant_id)
        # Site-scoped rules are more specific than tenant defaults. Keep the
        # ordering deterministic so a conflict never depends on database row
        # order or creation timing.
        rules = sorted(
            rules,
            key=lambda rule: (
                0 if rule.conditions.site_id == site_id else 1,
                rule.sort_order,
                rule.rule_id,
            ),
        )
        executions: list[AutomationExecution] = []
        queue_claimed = False
        priority_claimed = False
        applied_tags: set[str] = set()
        for rule in rules:
            if not rule.enabled:
                continue
            result = _match(rule.conditions, facts)
            actions = rule.actions if result.matched else AutomationActions()
            execution_reasons = list(result.reasons)
            if result.matched:
                # Routing fields are single-writer: deterministic rule order decides the
                # winner, while tags remain additive. A direct handoff is terminal.
                if actions.queue_id and queue_claimed:
                    execution_reasons.append("queue_conflict:first_matching_rule_wins")
                if actions.priority and priority_claimed:
                    execution_reasons.append("priority_conflict:first_matching_rule_wins")
                actions = replace(
                    actions,
                    queue_id=actions.queue_id if not queue_claimed else None,
                    priority=actions.priority if not priority_claimed else None,
                    tags=tuple(tag for tag in actions.tags if tag not in applied_tags),
                )
                queue_claimed = queue_claimed or bool(actions.queue_id)
                priority_claimed = priority_claimed or bool(actions.priority)
                applied_tags.update(actions.tags)
            action_names = _action_names(actions)
            execution_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"automation:{command.principal.tenant_id}:{rule.rule_id}:{command.request_id}",
                )
            )
            execution = AutomationExecution(
                execution_id=execution_id,
                tenant_id=command.principal.tenant_id,
                rule_id=rule.rule_id,
                conversation_id=command.conversation_id,
                matched=result.matched,
                reasons=tuple(execution_reasons),
                actions_applied=action_names,
                occurred_at=now,
            )
            executions.append(
                await self._port.record_automation_execution(
                    execution=execution,
                    actions=actions,
                    actor_subject_id="automation-engine",
                    correlation_id=command.principal.correlation_id,
                    idempotency_key=sha256(
                        f"automation:{rule.rule_id}:{command.request_id}".encode()
                    ).hexdigest(),
                )
            )
            if result.matched and rule.actions.direct_handoff:
                break
        return tuple(executions)

    async def list_executions(
        self, query: ListAutomationExecutionsQuery
    ) -> CustomerExperienceResult:
        require_scope(query.principal, "automation:read")
        if not 1 <= query.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        items = await self._port.list_automation_executions(
            tenant_id=query.principal.tenant_id, limit=query.limit
        )
        return CustomerExperienceResult(automation_executions=tuple(items))

    async def submit_satisfaction(self, command: SubmitSatisfactionCommand) -> None:
        if command.principal.site_id is None:
            raise PermissionError("widget site identity is required")
        visitor_session_id = _public_visitor_session_id(command.principal)
        if not 1 <= command.score <= 5:
            raise ValueError("score must be between 1 and 5")
        comment = redact_sensitive_text(command.comment.strip()) if command.comment else None
        if comment and len(comment) > 1000:
            raise ValueError("comment must contain at most 1000 characters")
        await self._port.submit_satisfaction(
            tenant_id=command.principal.tenant_id,
            site_id=command.principal.site_id,
            conversation_id=_opaque(command.conversation_id, "conversation_id"),
            rating_id=str(
                uuid5(
                    NAMESPACE_URL, f"csat:{command.principal.tenant_id}:{command.conversation_id}"
                )
            ),
            score=command.score,
            comment=comment,
            idempotency_key=f"csat:{command.request_id}",
            created_at=datetime.now(UTC),
            visitor_session_id=visitor_session_id,
        )

    async def create_offline_ticket(self, command: CreateOfflineTicketCommand) -> str:
        if command.principal.site_id is None:
            raise PermissionError("widget site identity is required")
        visitor_session_id = _public_visitor_session_id(command.principal)
        email = command.email.strip().casefold()
        if len(email) > 254 or "@" not in email or any(char.isspace() for char in email):
            raise ValueError("a valid contact email is required")
        message = redact_sensitive_text(_text(command.message, "message", 5000))
        return await self._port.create_offline_ticket(
            tenant_id=command.principal.tenant_id,
            site_id=command.principal.site_id,
            conversation_id=_opaque(command.conversation_id, "conversation_id"),
            email=email,
            message=message,
            page_path=command.page_path[:500],
            idempotency_key=f"offline:{command.request_id}",
            created_at=datetime.now(UTC),
            visitor_session_id=visitor_session_id,
        )

    async def list_knowledge_gaps(self, query: ListKnowledgeGapsQuery) -> CustomerExperienceResult:
        require_scope(query.principal, "support:inbox:read")
        if query.status not in {None, "open", "resolved"}:
            raise ValueError("status must be open or resolved")
        if not 1 <= query.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        gaps = await self._port.list_knowledge_gaps(
            tenant_id=query.principal.tenant_id, status=query.status, limit=query.limit
        )
        return CustomerExperienceResult(knowledge_gaps=tuple(gaps))

    async def create_knowledge_gap(self, command: CreateKnowledgeGapCommand) -> KnowledgeGap:
        require_scope(command.principal, "support:inbox:write")
        _idempotency(command.idempotency_key)
        category = command.category.strip().casefold()
        if category not in {"missing_knowledge", "incorrect_answer"}:
            raise ValueError("unsupported knowledge gap category")
        now = datetime.now(UTC)
        gap = KnowledgeGap(
            gap_id=str(uuid4()),
            tenant_id=command.principal.tenant_id,
            conversation_id=_opaque(command.conversation_id, "conversation_id"),
            source="agent_feedback",
            category=category,
            summary=redact_sensitive_text(_text(command.summary, "summary", 2000)),
            status=KnowledgeGapStatus.OPEN,
            created_by=command.principal.subject_id,
            created_at=now,
        )
        return await self._port.create_knowledge_gap(
            gap=gap,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
        )

    async def resolve_knowledge_gap(self, command: ResolveKnowledgeGapCommand) -> KnowledgeGap:
        require_scope(command.principal, "support:inbox:write")
        _idempotency(command.idempotency_key)
        return await self._port.resolve_knowledge_gap(
            tenant_id=command.principal.tenant_id,
            gap_id=_opaque(command.gap_id, "gap_id"),
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            resolution_note=redact_sensitive_text(
                _text(command.resolution_note, "resolution_note", 2000)
            ),
            idempotency_key=command.idempotency_key,
            resolved_at=datetime.now(UTC),
        )

    async def summary(self, query: GetExperienceSummaryQuery) -> CustomerExperienceResult:
        require_scope(query.principal, "support:inbox:read")
        if not 1 <= query.days <= 365:
            raise ValueError("days must be between 1 and 365")
        summary = await self._port.experience_summary(
            tenant_id=query.principal.tenant_id, days=query.days, site_id=query.site_id
        )
        return CustomerExperienceResult(summary=summary)


def default_widget_config(language: str = "en") -> WidgetConfig:
    normalized = language.strip() or "en"
    return WidgetConfig(
        welcome_message="您好！今天有什么可以帮您？"
        if normalized.startswith("zh")
        else "Hello! How can I help you today?",
        online_message="客服在线",
        offline_message="当前为非工作时间，请留言，我们会尽快回复。",
        business_timezone="Asia/Shanghai",
        business_hours={
            "mon": "09:00-18:00",
            "tue": "09:00-18:00",
            "wed": "09:00-18:00",
            "thu": "09:00-18:00",
            "fri": "09:00-18:00",
        },
        holidays=(),
        offline_form_enabled=True,
        primary_color="#2563eb",
        position="right",
        agent_name="在线客服",
        agent_avatar_url=None,
        mobile_enabled=True,
        default_language=normalized,
        handoff_timeout_seconds=120,
        csat_enabled=True,
        customer_address_mode="neutral",
        introduce_on_first_turn=True,
        launcher_asset_id=None,
        launcher_image_fit="contain",
        agent_avatar_asset_id=None,
    )


def validate_widget_config(config: WidgetConfig) -> WidgetConfig:
    try:
        ZoneInfo(config.business_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("business_timezone is not recognized") from exc
    if not _COLOR.fullmatch(config.primary_color):
        raise ValueError("primary_color must be a six-digit hex color")
    if config.position not in {"left", "right"}:
        raise ValueError("position must be left or right")
    if not _LANGUAGE.fullmatch(config.default_language):
        raise ValueError("default_language must be a valid language tag")
    if not 30 <= config.handoff_timeout_seconds <= 3600:
        raise ValueError("handoff_timeout_seconds must be between 30 and 3600")
    if config.customer_address_mode not in {"formal", "neutral", "friendly"}:
        raise ValueError("customer_address_mode must be formal, neutral, or friendly")
    if config.launcher_image_fit not in {"contain", "cover"}:
        raise ValueError("launcher_image_fit must be contain or cover")
    for asset_id in (config.launcher_asset_id, config.agent_avatar_asset_id):
        if asset_id is not None:
            _opaque(asset_id, "widget asset id")
    valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    if set(config.business_hours) - valid_days:
        raise ValueError("business_hours contains an unsupported weekday")
    if any(not _TIME_RANGE.fullmatch(value) for value in config.business_hours.values()):
        raise ValueError("business hour ranges must use HH:MM-HH:MM")
    try:
        for holiday in config.holidays:
            date.fromisoformat(holiday)
    except ValueError as exc:
        raise ValueError("holidays must use YYYY-MM-DD") from exc
    for value in (config.welcome_message, config.online_message, config.offline_message):
        _text(value, "widget message", 500)
    _text(config.agent_name, "agent_name", 100)
    if config.agent_avatar_url and not config.agent_avatar_url.startswith(("https://", "http://")):
        raise ValueError("agent_avatar_url must be an absolute HTTP URL")
    return config


def validate_conditions(conditions: AutomationConditions) -> AutomationConditions:
    if conditions.user_intent and conditions.user_intent not in _ALLOWED_INTENTS:
        raise ValueError("unsupported automation intent")
    if conditions.minimum_risk_level is not None and not 0 <= conditions.minimum_risk_level <= 3:
        raise ValueError("minimum_risk_level must be between 0 and 3")
    if (
        conditions.minimum_dwell_seconds is not None
        and not 0 <= conditions.minimum_dwell_seconds <= 86_400
    ):
        raise ValueError("minimum_dwell_seconds must be between 0 and 86400")
    if conditions.page_path_prefix and not conditions.page_path_prefix.startswith("/"):
        raise ValueError("page_path_prefix must be a relative path")
    return conditions


def validate_actions(actions: AutomationActions) -> AutomationActions:
    if actions.priority and actions.priority not in _ALLOWED_PRIORITIES:
        raise ValueError("unsupported automation priority")
    tags = tuple(dict.fromkeys(tag.strip().casefold() for tag in actions.tags if tag.strip()))
    if len(tags) > 20 or any(len(tag) > 50 for tag in tags):
        raise ValueError("automation tags exceed limits")
    if not any(
        (actions.queue_id, actions.priority, tags, actions.create_ticket, actions.direct_handoff)
    ):
        raise ValueError("automation rule must define at least one action")
    return AutomationActions(
        queue_id=actions.queue_id,
        priority=actions.priority,
        tags=tags,
        create_ticket=actions.create_ticket,
        direct_handoff=actions.direct_handoff,
    )


def _match(conditions: AutomationConditions, facts: AutomationFacts) -> AutomationTestResult:
    failures: list[str] = []
    checks = (
        (conditions.site_id, facts.site_id, "site_id"),
        (conditions.business_hours, facts.within_business_hours, "business_hours"),
        (conditions.user_intent, facts.user_intent, "user_intent"),
        (conditions.authenticated, facts.authenticated, "authenticated"),
        (conditions.has_assignee, facts.has_assignee, "has_assignee"),
        (conditions.has_ticket, facts.has_ticket, "has_ticket"),
    )
    for expected, actual, name in checks:
        if expected is not None and expected != actual:
            failures.append(f"{name}:expected={expected},actual={actual}")
    if conditions.page_path_prefix and not facts.page_path.startswith(conditions.page_path_prefix):
        failures.append("page_path_prefix:not_matched")
    if (
        conditions.minimum_risk_level is not None
        and facts.risk_level < conditions.minimum_risk_level
    ):
        failures.append("minimum_risk_level:not_reached")
    if (
        conditions.minimum_dwell_seconds is not None
        and facts.dwell_seconds < conditions.minimum_dwell_seconds
    ):
        failures.append("minimum_dwell_seconds:not_reached")
    return AutomationTestResult(not failures, tuple(failures) or ("all_conditions_matched",))


def _action_names(actions: AutomationActions) -> tuple[str, ...]:
    values: list[str] = []
    if actions.queue_id:
        values.append("assign_queue")
    if actions.priority:
        values.append("set_priority")
    if actions.tags:
        values.append("add_tags")
    if actions.create_ticket:
        values.append("create_ticket")
    if actions.direct_handoff:
        values.append("direct_handoff")
    return tuple(values)


def _text(value: str, name: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must contain between 1 and {maximum} characters")
    return normalized


def _opaque(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 100:
        raise ValueError(f"{name} must be a bounded opaque identifier")
    return normalized


def _public_visitor_session_id(principal: AuthenticatedPrincipal) -> str | None:
    if principal.authentication_method != "public_widget_token":
        return None
    if not principal.visitor_session_id:
        raise PermissionError("public widget visitor session is required")
    return _opaque(principal.visitor_session_id, "visitor_session_id")


def _idempotency(value: str) -> None:
    if not value.strip() or len(value) > 200:
        raise ValueError("idempotency key must contain between 1 and 200 characters")
