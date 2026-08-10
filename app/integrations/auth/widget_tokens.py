import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from app.domain.models.widget import (
    IssuedPublicWidgetToken,
    PublicPresenceTokenClaims,
    PublicWidgetSite,
    PublicWidgetTokenClaims,
)


class HmacPublicWidgetTokenAdapter:
    def __init__(self, *, secret: str, ttl_seconds: int) -> None:
        if len(secret) < 32:
            raise ValueError("widget token secret must contain at least 32 characters")
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError("widget token TTL must be between 60 and 3600 seconds")
        self._secret = secret.encode("utf-8")
        self._ttl = timedelta(seconds=ttl_seconds)

    def issue(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        issued_at: datetime,
        session_id: str | None = None,
    ) -> IssuedPublicWidgetToken:
        expires_at = issued_at + self._ttl
        stable_session_id = session_id or token_urlsafe(24)
        payload = {
            "exp": int(expires_at.timestamp()),
            "iat": int(issued_at.timestamp()),
            "jti": token_urlsafe(12),
            "origin": origin,
            "public_widget_id": site.public_widget_id,
            "sid": stable_session_id,
            "auth_version": site.auth_version,
            "scopes": ["chat:public", "handoff:create", "presence:write"],
            "v": 2,
        }
        encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return IssuedPublicWidgetToken(f"{encoded}.{signature}", expires_at)

    def verify(self, *, token: str, verified_at: datetime) -> PublicWidgetTokenClaims:
        try:
            encoded, signature = token.split(".", maxsplit=1)
            expected = _encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise PermissionError("invalid public widget token")
            payload = json.loads(_decode(encoded))
            if not isinstance(payload, dict) or payload.get("v") not in {1, 2}:
                raise PermissionError("invalid public widget token")
            issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
            expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
            now = verified_at.astimezone(UTC)
            if expires_at <= now or issued_at > now + timedelta(seconds=30):
                raise PermissionError("expired public widget token")
            scopes = payload.get("scopes")
            if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
                raise PermissionError("invalid public widget token")
            token_version = int(payload["v"])
            token_id = _required_string(payload, "jti")
            return PublicWidgetTokenClaims(
                token_id=token_id,
                public_widget_id=_required_string(payload, "public_widget_id"),
                origin=_required_string(payload, "origin"),
                scopes=frozenset(scopes),
                issued_at=issued_at,
                expires_at=expires_at,
                visitor_session_id=(
                    _required_string(payload, "sid") if token_version == 2 else token_id
                ),
                auth_version=int(payload.get("auth_version", 1)),
                token_version=token_version,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PermissionError("invalid public widget token") from exc

    def issue_presence(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        visitor_id: str,
        issued_at: datetime,
    ) -> IssuedPublicWidgetToken:
        expires_at = issued_at + self._ttl
        payload = {
            "auth_version": site.auth_version,
            "exp": int(expires_at.timestamp()),
            "iat": int(issued_at.timestamp()),
            "jti": token_urlsafe(12),
            "origin": origin,
            "public_widget_id": site.public_widget_id,
            "scopes": ["presence:write"],
            "visitor_hash": self._presence_visitor_hash(visitor_id),
            "v": 3,
        }
        encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return IssuedPublicWidgetToken(f"{encoded}.{signature}", expires_at)

    def verify_presence(
        self,
        *,
        token: str,
        visitor_id: str,
        verified_at: datetime,
    ) -> PublicPresenceTokenClaims:
        try:
            encoded, signature = token.split(".", maxsplit=1)
            expected = _encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise PermissionError("invalid public presence token")
            payload = json.loads(_decode(encoded))
            if not isinstance(payload, dict) or payload.get("v") != 3:
                raise PermissionError("invalid public presence token")
            scopes = payload.get("scopes")
            if scopes != ["presence:write"]:
                raise PermissionError("invalid public presence token")
            issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
            expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
            now = verified_at.astimezone(UTC)
            if expires_at <= now or issued_at > now + timedelta(seconds=30):
                raise PermissionError("expired public presence token")
            visitor_id_hash = _required_string(payload, "visitor_hash")
            if not hmac.compare_digest(visitor_id_hash, self._presence_visitor_hash(visitor_id)):
                raise PermissionError("public presence token visitor mismatch")
            return PublicPresenceTokenClaims(
                token_id=_required_string(payload, "jti"),
                public_widget_id=_required_string(payload, "public_widget_id"),
                origin=_required_string(payload, "origin"),
                visitor_id_hash=visitor_id_hash,
                issued_at=issued_at,
                expires_at=expires_at,
                auth_version=int(payload.get("auth_version", 1)),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PermissionError("invalid public presence token") from exc

    def _presence_visitor_hash(self, visitor_id: str) -> str:
        return _encode(
            hmac.new(
                self._secret,
                f"presence-visitor:{visitor_id}".encode(),
                hashlib.sha256,
            ).digest()
        )


class HmacPublicWidgetCursorAdapter:
    def __init__(self, *, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("widget cursor secret must contain at least 32 characters")
        self._secret = secret.encode("utf-8")

    def issue(
        self,
        *,
        tenant_id: str,
        site_id: str,
        session_id: str,
        conversation_id: str,
        after_id: int,
    ) -> str:
        if after_id < 0:
            raise ValueError("message cursor cannot be negative")
        payload = {
            "after": after_id,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "site_id": site_id,
            "tenant_id": tenant_id,
            "v": 1,
        }
        encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(
        self,
        *,
        cursor: str,
        tenant_id: str,
        site_id: str,
        session_id: str,
        conversation_id: str,
    ) -> int:
        try:
            encoded, signature = cursor.split(".", maxsplit=1)
            expected = _encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise PermissionError("invalid public widget cursor")
            payload = json.loads(_decode(encoded))
            expected_context = {
                "tenant_id": tenant_id,
                "site_id": site_id,
                "session_id": session_id,
                "conversation_id": conversation_id,
            }
            if not isinstance(payload, dict) or payload.get("v") != 1:
                raise PermissionError("invalid public widget cursor")
            if any(payload.get(key) != value for key, value in expected_context.items()):
                raise PermissionError("invalid public widget cursor")
            after_id = int(payload["after"])
            if after_id < 0:
                raise PermissionError("invalid public widget cursor")
            return after_id
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PermissionError("invalid public widget cursor") from exc


def _required_string(payload: dict, key: str) -> str:  # type: ignore[type-arg]
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PermissionError("invalid public widget token")
    return value


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")
