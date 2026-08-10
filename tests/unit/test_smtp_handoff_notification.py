from email.message import EmailMessage

from app.domain.models import HandoffNotification
from app.integrations.notifications.smtp import SmtpHandoffNotificationAdapter


async def test_smtp_handoff_notification_builds_redacted_site_email(monkeypatch) -> None:
    sent: list[tuple[EmailMessage, tuple[str, ...]]] = []
    adapter = SmtpHandoffNotificationAdapter(
        host="smtp.example.com",
        port=587,
        from_address="support@example.com",
        recipients_by_site={
            "site-a": ["agent@example.com", "agent@example.com", "backup@example.com"]
        },
        username="support@example.com",
        password="not-a-real-secret",
    )

    def fake_send_sync(message: EmailMessage, recipients: tuple[str, ...]) -> None:
        sent.append((message, recipients))

    monkeypatch.setattr(adapter, "_send_sync", fake_send_sync)

    await adapter.send(
        HandoffNotification(
            tenant_id="tenant-a",
            site_id="site-a",
            handoff_id="handoff-1",
            conversation_id="conversation-1",
            reason_code="knowledge_insufficient",
            risk_level=1,
            summary="已脱敏的人工处理摘要",
        )
    )

    assert len(sent) == 1
    message, recipients = sent[0]
    assert recipients == ("agent@example.com", "backup@example.com")
    assert message["From"] == "support@example.com"
    assert message["To"] == "agent@example.com, backup@example.com"
    assert message["Message-ID"] == "<handoff-1@support-agent.local>"
    body = message.get_content()
    assert "site-a" in body
    assert "conversation-1" in body
    assert "knowledge_insufficient" in body
    assert "已脱敏的人工处理摘要" in body
    assert "tenant-a" not in body


async def test_smtp_handoff_notification_skips_unmapped_site(monkeypatch) -> None:
    adapter = SmtpHandoffNotificationAdapter(
        host="smtp.example.com",
        port=587,
        from_address="support@example.com",
        recipients_by_site={},
    )

    def fail_if_called(message: EmailMessage, recipients: tuple[str, ...]) -> None:
        raise AssertionError("SMTP must not be called for an unmapped site")

    monkeypatch.setattr(adapter, "_send_sync", fail_if_called)

    await adapter.send(
        HandoffNotification(
            tenant_id="tenant-a",
            site_id="site-unmapped",
            handoff_id="handoff-1",
            conversation_id="conversation-1",
            reason_code="knowledge_insufficient",
            risk_level=1,
            summary="需要人工处理",
        )
    )
