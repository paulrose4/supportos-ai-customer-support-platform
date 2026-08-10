import asyncio
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from uuid import uuid4


class SmtpTransactionalEmailAdapter:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        username: str | None = None,
        password: str | None = None,
        use_starttls: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._from_address = from_address
        self._username = username
        self._password = password
        self._use_starttls = use_starttls
        self._timeout_seconds = timeout_seconds

    async def send_invitation(
        self,
        *,
        recipient: str,
        inviter_name: str,
        tenant_name: str,
        invitation_url: str,
        expires_at: datetime,
    ) -> None:
        message = self._message(
            recipient=recipient,
            subject=f"加入 {tenant_name} 客服工作区",
            body="\n".join(
                (
                    f"{inviter_name} 邀请你加入 {tenant_name} 客服工作区。",
                    "",
                    f"注册链接：{invitation_url}",
                    f"有效期至：{expires_at.isoformat()}",
                    "",
                    "该链接仅限一次使用。如果你不认识邀请人，请忽略此邮件。",
                )
            ),
        )
        await asyncio.to_thread(self._send_sync, message, recipient)

    async def send_email_verification(
        self,
        *,
        recipient: str,
        display_name: str,
        workspace_name: str,
        verification_url: str,
        expires_at: datetime,
    ) -> None:
        message = self._message(
            recipient=recipient,
            subject="验证邮箱并创建客服工作区",
            body="\n".join(
                (
                    f"{display_name}，你好：",
                    "",
                    f"你正在创建 {workspace_name} 客服工作区。",
                    f"邮箱验证链接：{verification_url}",
                    f"有效期至：{expires_at.isoformat()}",
                    "",
                    "链接仅限一次使用。如果这不是你的操作，请忽略此邮件。",
                )
            ),
        )
        await asyncio.to_thread(self._send_sync, message, recipient)

    async def send_password_reset(
        self,
        *,
        recipient: str,
        display_name: str,
        reset_url: str,
        expires_at: datetime,
    ) -> None:
        message = self._message(
            recipient=recipient,
            subject="重置客服工作台登录密码",
            body="\n".join(
                (
                    f"{display_name}，你好：",
                    "",
                    f"密码重置链接：{reset_url}",
                    f"有效期至：{expires_at.isoformat()}",
                    "",
                    "该链接仅限一次使用。如果这不是你的操作，请忽略此邮件。",
                )
            ),
        )
        await asyncio.to_thread(self._send_sync, message, recipient)

    def _message(self, *, recipient: str, subject: str, body: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._from_address
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = f"<{uuid4()}@support-agent.local>"
        message.set_content(body)
        return message

    def _send_sync(self, message: EmailMessage, recipient: str) -> None:
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout_seconds) as client:
                if self._use_starttls:
                    client.starttls(context=ssl.create_default_context())
                if self._username:
                    client.login(self._username, self._password or "")
                client.send_message(message, to_addrs=[recipient])
        except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
            raise ConnectionError("transactional email delivery failed") from exc
