from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_CURRENT_TENANT_ID: ContextVar[str | None] = ContextVar(
    "application_current_tenant_id",
    default=None,
)
_GLOBAL_KNOWLEDGE_ACCESS: ContextVar[bool] = ContextVar(
    "application_global_knowledge_access",
    default=False,
)


def current_tenant_id() -> str | None:
    return _CURRENT_TENANT_ID.get()


def global_knowledge_access_enabled() -> bool:
    return _GLOBAL_KNOWLEDGE_ACCESS.get()


def bind_tenant(tenant_id: str) -> Token[str | None]:
    value = tenant_id.strip()
    if not value or value == "__global__":
        raise ValueError("a concrete tenant_id is required")
    return _CURRENT_TENANT_ID.set(value)


def reset_tenant(token: Token[str | None]) -> None:
    _CURRENT_TENANT_ID.reset(token)


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[None]:
    token = bind_tenant(tenant_id)
    try:
        yield
    finally:
        reset_tenant(token)


@contextmanager
def global_knowledge_scope() -> Iterator[None]:
    token = _GLOBAL_KNOWLEDGE_ACCESS.set(True)
    try:
        yield
    finally:
        _GLOBAL_KNOWLEDGE_ACCESS.reset(token)
