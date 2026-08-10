from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.ports import RouteLatencySnapshot, SupportAnalyticsSnapshot


class PostgreSQLSupportAnalyticsAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def overview(
        self, *, tenant_id: str, days: int, site_id: str | None
    ) -> SupportAnalyticsSnapshot:
        since = datetime.now(UTC) - timedelta(days=days)
        site_clause = " AND c.site_id = :site_id" if site_id else ""
        query = text(f"""
            SELECT
              count(DISTINCT c.conversation_id) AS conversations,
              count(DISTINCT ar.run_id) AS agent_runs,
              count(DISTINCT ar.run_id) FILTER (WHERE ar.response_kind = 'answer') AS ai_answers,
              count(DISTINCT ar.run_id) FILTER (WHERE ar.response_kind = 'handoff') AS handoffs,
              count(DISTINCT ar.run_id) FILTER (
                WHERE h.reason_code LIKE 'order_%'
              ) AS forced_order_handoffs,
              count(DISTINCT ar.run_id) FILTER (
                WHERE h.reason_code IS NULL OR h.reason_code NOT LIKE 'order_%'
              ) AS ai_eligible_runs,
              count(DISTINCT c.conversation_id) FILTER (
                WHERE EXISTS (
                  SELECT 1 FROM messages m
                  WHERE m.tenant_id = c.tenant_id
                    AND m.conversation_id = c.conversation_id
                    AND m.role = 'agent'
                    AND m.message_type = 'chat'
                )
              ) AS human_replied,
              count(DISTINCT c.conversation_id) FILTER (WHERE c.status='resolved') AS resolved,
              coalesce(avg(extract(epoch FROM (c.first_response_at - c.created_at)))
                FILTER (WHERE c.first_response_at IS NOT NULL), 0) AS avg_first_response,
              coalesce(avg(extract(epoch FROM (c.first_human_response_at - c.created_at)))
                FILTER (WHERE c.first_human_response_at IS NOT NULL), 0) AS avg_human_response,
              coalesce(avg(extract(epoch FROM (c.resolved_at - c.created_at)))
                FILTER (WHERE c.resolved_at IS NOT NULL), 0) AS avg_resolution,
              count(DISTINCT c.conversation_id) FILTER (WHERE c.unread_count > 0) AS unread,
              count(DISTINCT c.conversation_id) FILTER (
                WHERE c.status='waiting_human'
              ) AS waiting_human,
              count(DISTINCT ar.run_id) FILTER (
                WHERE ar.request_received_at IS NOT NULL AND ar.response_sent_at IS NOT NULL
              ) AS latency_samples,
              coalesce(percentile_cont(0.50) WITHIN GROUP (
                ORDER BY ar.response_latency_ms
              ) FILTER (WHERE ar.response_latency_ms IS NOT NULL), 0) AS latency_p50_ms,
              coalesce(percentile_cont(0.95) WITHIN GROUP (
                ORDER BY ar.response_latency_ms
              ) FILTER (WHERE ar.response_latency_ms IS NOT NULL), 0) AS latency_p95_ms,
              coalesce(percentile_cont(0.99) WITHIN GROUP (
                ORDER BY ar.response_latency_ms
              ) FILTER (WHERE ar.response_latency_ms IS NOT NULL), 0) AS latency_p99_ms,
              count(DISTINCT c.conversation_id) FILTER (
                WHERE NOT EXISTS (
                  SELECT 1 FROM handoff_requests h
                  WHERE h.tenant_id=c.tenant_id AND h.conversation_id=c.conversation_id
                )
                AND NOT EXISTS (
                  SELECT 1 FROM messages hm
                  WHERE hm.tenant_id=c.tenant_id AND hm.conversation_id=c.conversation_id
                    AND hm.role='agent' AND hm.message_type='chat'
                )
                AND (
                  c.auto_resolution_eligible_at <= now()
                  OR c.resolution_source IN ('customer_confirmation', 'auto_inactivity')
                )
              ) AS auto_resolution_eligible,
              count(DISTINCT c.conversation_id) FILTER (
                WHERE c.status='resolved'
                  AND c.resolution_source IN ('customer_confirmation', 'auto_inactivity')
              ) AS auto_resolved,
              count(DISTINCT c.conversation_id) FILTER (
                WHERE c.reopened_at IS NOT NULL
              ) AS reopened,
              count(DISTINCT c.conversation_id) FILTER (
                WHERE c.reopened_at IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM resolution_episodes re
                    WHERE re.tenant_id=c.tenant_id
                      AND re.conversation_id=c.conversation_id
                      AND re.resolution_source='auto_inactivity'
                  )
              ) AS auto_resolution_reopened,
              count(DISTINCT c.conversation_id) FILTER (
                WHERE c.status='resolved'
                  AND c.resolution_source='auto_inactivity'
                  AND c.risk_level >= 2
              ) AS high_risk_auto_resolved
            FROM conversations c
            LEFT JOIN agent_runs ar
              ON ar.tenant_id = c.tenant_id
             AND ar.conversation_id = c.conversation_id
            LEFT JOIN handoff_requests h
              ON h.tenant_id = ar.tenant_id
             AND h.handoff_id = ar.handoff_id
            WHERE c.tenant_id=:tenant_id AND c.created_at>=:since{site_clause}
        """)
        values = {"tenant_id": tenant_id, "since": since, "site_id": site_id}
        async with self._session_factory() as session:
            row = (await session.execute(query, values)).mappings().one()
            route_rows = (
                (
                    await session.execute(
                        text(f"""
                        SELECT coalesce(ar.request_route, 'unknown') AS route,
                               count(*) AS samples,
                               percentile_cont(0.50) WITHIN GROUP (
                                 ORDER BY ar.response_latency_ms
                               ) AS p50_ms,
                               percentile_cont(0.95) WITHIN GROUP (
                                 ORDER BY ar.response_latency_ms
                               ) AS p95_ms,
                               percentile_cont(0.99) WITHIN GROUP (
                                 ORDER BY ar.response_latency_ms
                               ) AS p99_ms
                        FROM agent_runs ar
                        JOIN conversations c
                          ON c.tenant_id=ar.tenant_id
                         AND c.conversation_id=ar.conversation_id
                        WHERE c.tenant_id=:tenant_id AND c.created_at>=:since
                          AND ar.response_latency_ms IS NOT NULL{site_clause}
                        GROUP BY coalesce(ar.request_route, 'unknown')
                        ORDER BY route
                    """),
                        values,
                    )
                )
                .mappings()
                .all()
            )
            handoff_row = (
                (
                    await session.execute(
                        text(f"""
                        SELECT count(*) FILTER (
                                 WHERE h.context_schema_version=2
                               ) AS total,
                               count(*) FILTER (
                                 WHERE h.context_schema_version<2
                               ) AS legacy,
                               count(*) FILTER (
                                 WHERE h.context_schema_version=2
                                   AND nullif(btrim(h.summary), '') IS NOT NULL
                                   AND nullif(btrim(h.customer_language), '') IS NOT NULL
                                   AND nullif(btrim(h.identity_status), '') IS NOT NULL
                                   AND nullif(btrim(h.user_intent), '') IS NOT NULL
                                   AND nullif(btrim(h.unresolved_question), '') IS NOT NULL
                                   AND nullif(btrim(h.ai_attempt), '') IS NOT NULL
                                   AND nullif(btrim(h.suggested_next_action), '') IS NOT NULL
                                   AND nullif(btrim(h.reply_draft), '') IS NOT NULL
                                   AND nullif(btrim(h.customer_request), '') IS NOT NULL
                                   AND nullif(btrim(h.customer_sentiment), '') IS NOT NULL
                                   AND (
                                     h.commitment_deadline IS NULL
                                     OR h.sla_policy_version IS NOT NULL
                                   )
                               ) AS complete
                        FROM handoff_requests h
                        JOIN conversations c
                          ON c.tenant_id=h.tenant_id
                         AND c.conversation_id=h.conversation_id
                        WHERE c.tenant_id=:tenant_id AND h.created_at>=:since{site_clause}
                    """),
                        values,
                    )
                )
                .mappings()
                .one()
            )
        return SupportAnalyticsSnapshot(
            days,
            site_id,
            int(row["conversations"]),
            int(row["agent_runs"]),
            int(row["ai_answers"]),
            int(row["handoffs"]),
            int(row["human_replied"]),
            int(row["resolved"]),
            float(row["avg_first_response"]),
            float(row["avg_human_response"]),
            float(row["avg_resolution"]),
            int(row["unread"]),
            int(row["waiting_human"]),
            int(row["latency_samples"]),
            float(row["latency_p50_ms"]) / 1000,
            float(row["latency_p95_ms"]) / 1000,
            float(row["latency_p99_ms"]) / 1000,
            tuple(
                RouteLatencySnapshot(
                    str(item["route"]),
                    int(item["samples"]),
                    float(item["p50_ms"]) / 1000,
                    float(item["p95_ms"]) / 1000,
                    float(item["p99_ms"]) / 1000,
                )
                for item in route_rows
            ),
            int(row["auto_resolution_eligible"]),
            int(row["auto_resolved"]),
            int(row["reopened"]),
            int(row["auto_resolution_reopened"]),
            int(row["high_risk_auto_resolved"]),
            int(handoff_row["total"]),
            int(handoff_row["complete"]),
            int(handoff_row["legacy"]),
            int(row["ai_eligible_runs"]),
            int(row["forced_order_handoffs"]),
        )
