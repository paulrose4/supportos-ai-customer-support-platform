import json
from asyncio import to_thread
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.domain.models import VerifiedExternalIdentity


class DingTalkIdentityProvider:
    provider_name = "dingtalk"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        organization_id: str,
        timeout_seconds: float = 10.0,
        authorization_endpoint: str = "https://login.dingtalk.com/oauth2/auth",
        token_endpoint: str = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
        user_endpoint: str = "https://api.dingtalk.com/v1.0/contact/users/me",
    ) -> None:
        if not client_id or not client_secret or not organization_id:
            raise ValueError("DingTalk client, secret, and organization configuration are required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._organization_id = organization_id
        self._timeout_seconds = timeout_seconds
        self._authorization_endpoint = authorization_endpoint
        self._token_endpoint = token_endpoint
        self._user_endpoint = user_endpoint

    def build_authorization_url(self, *, state: str, redirect_uri: str) -> str:
        parameters = urlencode(
            {
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "client_id": self._client_id,
                "scope": "openid corpid",
                "state": state,
                "prompt": "consent",
            }
        )
        return f"{self._authorization_endpoint}?{parameters}"

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> VerifiedExternalIdentity:
        del redirect_uri
        token_payload = await to_thread(
            _request_json,
            self._token_endpoint,
            "POST",
            {
                "clientId": self._client_id,
                "clientSecret": self._client_secret,
                "code": code,
                "grantType": "authorization_code",
            },
            {},
            self._timeout_seconds,
        )
        access_token = _required_string(token_payload, "accessToken")
        organization_id = _optional_string(token_payload, "corpId", "corp_id")
        if organization_id != self._organization_id:
            raise PermissionError(
                "DingTalk identity does not belong to the configured organization"
            )

        profile = await to_thread(
            _request_json,
            self._user_endpoint,
            "GET",
            None,
            {"x-acs-dingtalk-access-token": access_token},
            self._timeout_seconds,
        )
        provider_subject_id = _optional_string(profile, "unionId", "union_id")
        provider_user_id = _optional_string(profile, "openId", "open_id")
        if not provider_subject_id:
            raise PermissionError("DingTalk did not return a stable user identity")
        display_name = _optional_string(profile, "nick", "name") or "DingTalk User"
        return VerifiedExternalIdentity(
            provider=self.provider_name,
            organization_id=organization_id,
            provider_subject_id=provider_subject_id,
            provider_user_id=provider_user_id,
            display_name=display_name[:200],
        )


def _request_json(
    url: str,
    method: str,
    payload: Mapping[str, object] | None,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", **headers},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise PermissionError(f"DingTalk identity request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ConnectionError("DingTalk identity service is unavailable") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionError("DingTalk identity service returned an invalid response") from exc
    if not isinstance(decoded, dict):
        raise ConnectionError("DingTalk identity service returned an invalid response")
    return decoded


def _required_string(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise PermissionError(f"DingTalk response is missing {key}")
    return value


def _optional_string(values: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value:
            return value
    return None
