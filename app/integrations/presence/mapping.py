from app.domain.models import VisitorPresence

_MAX_ACTIVE_HEARTBEAT_GAP_SECONDS = 35


def merge_presence(
    current: VisitorPresence | None,
    incoming: VisitorPresence,
) -> VisitorPresence:
    if current is None:
        return incoming
    if incoming.last_seen_at < current.last_seen_at:
        return current
    if incoming.last_page_view_id is not None:
        page_changed = incoming.last_page_view_id != current.last_page_view_id
    else:
        page_changed = current.page_path != incoming.page_path
    heartbeat_gap_seconds = max(
        0,
        int((incoming.last_seen_at - current.last_seen_at).total_seconds()),
    )
    active_increment = (
        heartbeat_gap_seconds if heartbeat_gap_seconds <= _MAX_ACTIVE_HEARTBEAT_GAP_SECONDS else 0
    )
    return VisitorPresence(
        tenant_id=incoming.tenant_id,
        site_id=incoming.site_id,
        visitor_id=incoming.visitor_id,
        conversation_id=incoming.conversation_id or current.conversation_id,
        page_path=incoming.page_path,
        last_seen_at=incoming.last_seen_at,
        first_seen_at=current.first_seen_at or current.last_seen_at,
        page_kind=(
            incoming.page_kind
            if incoming.page_kind is not None
            else (None if page_changed else current.page_kind)
        ),
        page_title=incoming.page_title or current.page_title,
        referrer=incoming.referrer or current.referrer,
        ip_address=incoming.ip_address or current.ip_address,
        country_code=incoming.country_code or current.country_code,
        user_agent=incoming.user_agent or current.user_agent,
        browser=incoming.browser or current.browser,
        operating_system=incoming.operating_system or current.operating_system,
        device_type=incoming.device_type or current.device_type,
        language=incoming.language or current.language,
        timezone=incoming.timezone or current.timezone,
        page_view_count=current.page_view_count + int(page_changed),
        session_started_at=(
            current.session_started_at or current.first_seen_at or current.last_seen_at
        ),
        current_page_entered_at=(
            incoming.current_page_entered_at
            if page_changed
            else current.current_page_entered_at or current.last_seen_at
        ),
        last_page_view_id=incoming.last_page_view_id or current.last_page_view_id,
        widget_state=incoming.widget_state,
        presence_source=incoming.presence_source,
        runtime_version=incoming.runtime_version or current.runtime_version,
        config_version=incoming.config_version or current.config_version,
        connector_type=incoming.connector_type or current.connector_type,
        connector_version=incoming.connector_version or current.connector_version,
        session_active_dwell_seconds=(current.session_active_dwell_seconds + active_increment),
    )
