import base64
import binascii
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from app.application.dto.platform_identity import (
    AssignPlatformRoleCommand,
    CreateTenantCommand,
    GetPlatformSummaryQuery,
    GetPlatformTenantQuery,
    ListPlatformMembershipsQuery,
    ListPlatformOnboardingRecordsQuery,
    ListPlatformSiteRecordsQuery,
    ListPlatformTenantRecordsQuery,
    ListPlatformTenantsQuery,
    ListPlatformTenantsResult,
    ListPlatformUserRecordsQuery,
    ListPlatformUsersQuery,
    ListPlatformUsersResult,
    PlatformMembershipsResult,
    PlatformOnboardingRecord,
    PlatformOnboardingRecordsResult,
    PlatformSiteRecordsResult,
    PlatformSummaryResult,
    PlatformTenantRecordsResult,
    PlatformUserRecordsResult,
    RevokePlatformRoleCommand,
    UpsertTenantMembershipCommand,
)
from app.domain.models import PlatformSiteRecord, PlatformTenantRecord, Tenant, TenantMembership
from app.domain.ports import PlatformIdentityStorePort, PlatformSiteDirectoryPort
from app.domain.rules.rbac import ADMIN_ROLES, scopes_for_roles

PLATFORM_ROLES = frozenset({"platform_owner", "platform_operator", "platform_auditor"})
_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,99}$")


class PlatformIdentityService:
    def __init__(
        self,
        store: PlatformIdentityStorePort,
        site_directory: PlatformSiteDirectoryPort | None = None,
    ) -> None:
        self._store = store
        self._site_directory = site_directory or cast(PlatformSiteDirectoryPort, store)

    async def list_tenants(self, query: ListPlatformTenantsQuery) -> ListPlatformTenantsResult:
        _require_platform_access(query.principal.platform_roles)
        return ListPlatformTenantsResult(tuple(await self._store.list_tenants()))

    async def list_users(self, query: ListPlatformUsersQuery) -> ListPlatformUsersResult:
        _require_platform_access(query.principal.platform_roles)
        return ListPlatformUsersResult(tuple(await self._store.list_identity_users()))

    async def get_summary(self, query: GetPlatformSummaryQuery) -> PlatformSummaryResult:
        _require_platform_access(query.principal.platform_roles)
        return PlatformSummaryResult(
            await self._store.get_platform_summary(checked_at=datetime.now(UTC))
        )

    async def list_tenant_records(
        self, query: ListPlatformTenantRecordsQuery
    ) -> PlatformTenantRecordsResult:
        _require_platform_access(query.principal.platform_roles)
        search, status, limit = _clean_page_query(
            query.search, query.status, query.limit, {"active", "disabled"}
        )
        after_updated_at, after_id = _decode_cursor(query.cursor)
        items, total, has_more = await self._store.list_platform_tenant_records(
            search=search,
            status=status,
            limit=limit,
            after_updated_at=after_updated_at,
            after_tenant_id=after_id,
        )
        next_cursor = (
            _encode_cursor(items[-1].updated_at, items[-1].tenant_id)
            if has_more and items
            else None
        )
        return PlatformTenantRecordsResult(tuple(items), total, next_cursor)

    async def get_tenant_record(self, query: GetPlatformTenantQuery) -> PlatformTenantRecord:
        _require_platform_access(query.principal.platform_roles)
        item = await self._store.get_platform_tenant_record(tenant_id=query.tenant_id)
        if item is None:
            raise LookupError("workspace was not found")
        return item

    async def list_site_records(
        self, query: ListPlatformSiteRecordsQuery
    ) -> PlatformSiteRecordsResult:
        _require_platform_access(query.principal.platform_roles)
        search, status, limit = _clean_page_query(
            query.search, query.status, query.limit, {"active", "disabled"}
        )
        verification_status = query.verification_status.strip().casefold()
        if verification_status and verification_status not in {
            "pending",
            "verified",
            "failed",
            "expired",
        }:
            raise ValueError("verification status filter is invalid")
        tenant_id = None
        if query.tenant_id is not None:
            tenant_id = query.tenant_id.strip()
            if not tenant_id:
                raise ValueError("tenant_id must not be empty")
            if len(tenant_id) > 100:
                raise ValueError("tenant_id is too long")
        after_tenant_id, after_site_id = _decode_site_cursor(query.cursor)
        if tenant_id is not None and after_tenant_id is not None and after_tenant_id != tenant_id:
            raise ValueError("cursor does not belong to the requested workspace")
        checked_at = datetime.now(UTC)
        items, total, has_more = await self._site_directory.list_platform_site_records(
            search=search,
            tenant_id=tenant_id,
            status=status,
            verification_status=verification_status,
            include_disabled=query.include_disabled,
            limit=limit,
            after_tenant_id=after_tenant_id,
            after_site_id=after_site_id,
            checked_at=checked_at,
        )
        items = [_with_effective_verification_status(item, checked_at) for item in items]
        next_cursor = (
            _encode_site_cursor(
                items[-1].tenant_id,
                items[-1].site_id,
            )
            if has_more and items
            else None
        )
        return PlatformSiteRecordsResult(tuple(items), total, next_cursor)

    async def list_memberships(
        self, query: ListPlatformMembershipsQuery
    ) -> PlatformMembershipsResult:
        _require_platform_access(query.principal.platform_roles)
        tenant = await self._store.get_platform_tenant_record(tenant_id=query.tenant_id)
        if tenant is None:
            raise LookupError("workspace was not found")
        return PlatformMembershipsResult(
            tenant_name=tenant.name,
            items=tuple(await self._store.list_platform_memberships(tenant_id=query.tenant_id)),
        )

    async def list_user_records(
        self, query: ListPlatformUserRecordsQuery
    ) -> PlatformUserRecordsResult:
        _require_platform_access(query.principal.platform_roles)
        search, status, limit = _clean_page_query(
            query.search, query.status, query.limit, {"active", "disabled"}
        )
        after_updated_at, after_id = _decode_cursor(query.cursor)
        items, total, has_more = await self._store.list_platform_user_records(
            search=search,
            status=status,
            limit=limit,
            after_updated_at=after_updated_at,
            after_user_id=after_id,
        )
        next_cursor = (
            _encode_cursor(items[-1].updated_at, items[-1].user_id) if has_more and items else None
        )
        return PlatformUserRecordsResult(tuple(items), total, next_cursor)

    async def list_onboarding_records(
        self, query: ListPlatformOnboardingRecordsQuery
    ) -> PlatformOnboardingRecordsResult:
        _require_platform_access(query.principal.platform_roles)
        search, status, limit = _clean_page_query(
            query.search,
            query.status,
            query.limit,
            {"issued", "verification_pending", "completed", "expired", "revoked", "failed"},
        )
        checked_at = datetime.now(UTC)
        records = [
            _onboarding_record(item, checked_at)
            for item in await self._store.list_platform_onboarding_records(search=search)
        ]
        if status:
            records = [item for item in records if item.status == status]
        total = len(records)
        after_created_at, after_id = _decode_cursor(query.cursor)
        if after_created_at is not None and after_id is not None:
            records = [
                item
                for item in records
                if (item.created_at, item.code_id) < (after_created_at, after_id)
            ]
        page = records[: limit + 1]
        has_more = len(page) > limit
        page = page[:limit]
        next_cursor = (
            _encode_cursor(page[-1].created_at, page[-1].code_id) if has_more and page else None
        )
        return PlatformOnboardingRecordsResult(tuple(page), total, next_cursor)

    async def create_tenant(self, command: CreateTenantCommand) -> Tenant:
        _require_platform_operator(command.principal.platform_roles)
        tenant_id = command.tenant_id.strip().casefold()
        name = command.name.strip()
        if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
            raise ValueError("tenant_id must use 3-100 lowercase letters, numbers, or hyphens")
        if not name or len(name) > 200:
            raise ValueError("tenant name must contain between 1 and 200 characters")
        now = datetime.now(UTC)
        return await self._store.provision_tenant(
            Tenant(
                tenant_id=tenant_id,
                name=name,
                status="active",
                created_at=now,
                updated_at=now,
            ),
            actor_subject_id=command.principal.subject_id,
        )

    async def upsert_membership(self, command: UpsertTenantMembershipCommand) -> TenantMembership:
        _require_platform_operator(command.principal.platform_roles)
        roles = command.roles
        if not roles or not roles.issubset(ADMIN_ROLES):
            raise ValueError("one or more tenant roles are invalid")
        status = command.status.strip().casefold()
        if status not in {"active", "disabled"}:
            raise ValueError("membership status must be active or disabled")
        now = datetime.now(UTC)
        return await self._store.upsert_membership(
            TenantMembership(
                membership_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        f"membership:{command.tenant_id}:{command.user_id}",
                    )
                ),
                tenant_id=command.tenant_id,
                tenant_name=command.tenant_id,
                user_id=command.user_id,
                roles=roles,
                scopes=scopes_for_roles(roles),
                status=status,
                created_at=now,
                updated_at=now,
            ),
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
        )

    async def assign_platform_role(self, command: AssignPlatformRoleCommand) -> None:
        if "platform_owner" not in command.principal.platform_roles:
            raise PermissionError("platform owner permission is required")
        role = command.role.strip().casefold()
        if role not in PLATFORM_ROLES:
            raise ValueError("platform role is invalid")
        await self._store.assign_platform_role(
            user_id=command.user_id,
            role=role,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            assigned_at=datetime.now(UTC),
        )

    async def revoke_platform_role(self, command: RevokePlatformRoleCommand) -> None:
        if "platform_owner" not in command.principal.platform_roles:
            raise PermissionError("platform owner permission is required")
        role = command.role.strip().casefold()
        if role not in PLATFORM_ROLES:
            raise ValueError("platform role is invalid")
        await self._store.revoke_platform_role(
            user_id=command.user_id,
            role=role,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            revoked_at=datetime.now(UTC),
        )

    async def bootstrap_platform_owner(self, *, user_id: str) -> None:
        await self._store.assign_platform_role(
            user_id=user_id,
            role="platform_owner",
            actor_subject_id="system-bootstrap",
            correlation_id="system-bootstrap",
            assigned_at=datetime.now(UTC),
        )


def _require_platform_access(roles: frozenset[str]) -> None:
    if not roles.intersection(PLATFORM_ROLES):
        raise PermissionError("platform administration permission is required")


def _require_platform_operator(roles: frozenset[str]) -> None:
    if not roles.intersection({"platform_owner", "platform_operator"}):
        raise PermissionError("platform operator permission is required")


def _clean_page_query(
    search: str, status: str, limit: int, allowed_statuses: set[str]
) -> tuple[str, str, int]:
    normalized_search = search.strip()
    if len(normalized_search) > 200:
        raise ValueError("search is too long")
    normalized_status = status.strip().casefold()
    if normalized_status and normalized_status not in allowed_statuses:
        raise ValueError("status filter is invalid")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    return normalized_search, normalized_status, limit


def _encode_cursor(occurred_at: datetime, resource_id: str) -> str:
    payload = json.dumps([occurred_at.isoformat(), resource_id], separators=(",", ":")).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError
        occurred_at, resource_id = values
        if not isinstance(resource_id, str) or not resource_id:
            raise ValueError
        parsed = datetime.fromisoformat(str(occurred_at))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed, resource_id
    except (ValueError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc


def _encode_site_cursor(tenant_id: str, site_id: str) -> str:
    payload = json.dumps([tenant_id, site_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_site_cursor(
    cursor: str | None,
) -> tuple[str | None, str | None]:
    if not cursor:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError
        tenant_id, site_id = values
        if not isinstance(tenant_id, str) or not isinstance(site_id, str):
            raise ValueError
        if not tenant_id or not site_id:
            raise ValueError
        return tenant_id, site_id
    except (ValueError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc


def _with_effective_verification_status(
    item: PlatformSiteRecord,
    checked_at: datetime,
) -> PlatformSiteRecord:
    if (
        item.verification_status == "pending"
        and item.verification_expires_at is not None
        and item.verification_expires_at <= checked_at
    ):
        return replace(item, verification_status="expired")
    return item


def _onboarding_record(source, checked_at: datetime) -> PlatformOnboardingRecord:  # type: ignore[no-untyped-def]
    status = "issued"
    if source.code_status == "revoked":
        status = "revoked"
    elif source.code_status == "expired" or source.expires_at <= checked_at:
        status = "expired"
    elif source.code_status == "consumed" or source.intent_status == "completed":
        status = "completed"
    elif source.email_status == "failed":
        status = "failed"
    elif source.intent_status in {"created", "verification_sent"}:
        status = "verification_pending"
    return PlatformOnboardingRecord(
        code_id=source.code_id,
        target_email=source.target_email,
        status=status,
        expires_at=source.expires_at,
        created_by=source.created_by,
        created_by_name=source.created_by_name,
        created_at=source.created_at,
        workspace_name=source.workspace_name,
        tenant_id=source.proposed_tenant_id if status == "completed" else None,
        email_status=source.email_status,
        email_attempts=source.email_attempts or 0,
        email_sent_at=source.email_sent_at,
        email_last_error=source.email_last_error,
    )
