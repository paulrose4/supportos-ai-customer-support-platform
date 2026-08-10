import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.domain.models import HandoffNotification


class SmtpHandoffNotificationAdapter:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        recipients_by_site: dict[str, list[str]],
        username: str | None = None,
        password: str | None = None,
        use_starttls: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._from_address = from_address
        self._recipients_by_site = {
            site_id: tuple(dict.fromkeys(addresses))
            for site_id, addresses in recipients_by_site.items()
        }
        self._username = username
        self._password = password
        self._use_starttls = use_starttls
        self._timeout_seconds = timeout_seconds

    async def send(self, notification: HandoffNotification) -> None:
        recipients = self._recipients_by_site.get(notification.site_id, ())
        if not recipients:
            return
        message = EmailMessage()
        message["From"] = self._from_address
        message["To"] = ", ".join(recipients)
        message["Subject"] = f"[客服转接] {notification.site_id} 风险等级 {notification.risk_level}"
        message["Message-ID"] = f"<{notification.handoff_id}@support-agent.local>"
        message.set_content(
            "\n".join(
                (
                    f"站点：{notification.site_id}",
                    f"会话：{notification.conversation_id}",
                    f"原因：{notification.reason_code}",
                    f"风险等级：{notification.risk_level}",
                    "",
                    "脱敏摘要：",
                    notification.summary,
                    "",
                    "请登录客服 Dashboard 处理。",
                )
            )
        )
        await asyncio.to_thread(self._send_sync, message, recipients)

    def _send_sync(self, message: EmailMessage, recipients: tuple[str, ...]) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout_seconds) as client:
            if self._use_starttls:
                client.starttls(context=ssl.create_default_context())
            if self._username:
                client.login(self._username, self._password or "")
            client.send_message(message, to_addrs=list(recipients))
