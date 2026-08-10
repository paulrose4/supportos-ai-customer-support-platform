import pytest

from app.integrations.auth import dingtalk as dingtalk_module
from app.integrations.auth.dingtalk import DingTalkIdentityProvider


@pytest.mark.asyncio
async def test_dingtalk_rejects_identity_from_another_organization(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_request(url, method, payload, headers, timeout):  # type: ignore[no-untyped-def]
        del url, method, payload, headers, timeout
        return {"accessToken": "secret-token", "corpId": "corp-other"}

    monkeypatch.setattr(dingtalk_module, "_request_json", fake_request)
    provider = DingTalkIdentityProvider(
        client_id="client-a",
        client_secret="secret-a",
        organization_id="corp-allowed",
    )

    with pytest.raises(PermissionError, match="configured organization"):
        await provider.exchange_authorization_code(
            code="authorization-code",
            redirect_uri="https://app.test/callback",
        )
