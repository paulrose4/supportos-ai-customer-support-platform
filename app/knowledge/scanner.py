import re
from pathlib import Path
from typing import Protocol

_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class KnowledgeSourceScanner(Protocol):
    def root_for(self, tenant_id: str) -> Path: ...

    def scan(self, tenant_id: str) -> list[Path]: ...


class ObsidianVaultScanner:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def root_for(self, tenant_id: str) -> Path:
        del tenant_id
        return self._root

    def scan(self, tenant_id: str = "") -> list[Path]:
        del tenant_id
        return _scan_markdown_files(self._root)


class TenantObsidianVaultScanner:
    def __init__(self, tenants_root: Path, *, source_directory: str = "obsidian") -> None:
        self._tenants_root = tenants_root.resolve()
        if not source_directory or Path(source_directory).name != source_directory:
            raise ValueError("knowledge source directory must be a single path segment")
        self._source_directory = source_directory

    @property
    def root(self) -> Path:
        return self._tenants_root

    def root_for(self, tenant_id: str) -> Path:
        normalized_tenant_id = _validate_tenant_id(tenant_id)
        tenant_root = (self._tenants_root / normalized_tenant_id / self._source_directory).resolve()
        if not tenant_root.is_relative_to(self._tenants_root):
            raise ValueError("tenant knowledge path escaped configured root")
        return tenant_root

    def scan(self, tenant_id: str) -> list[Path]:
        return _scan_markdown_files(self.root_for(tenant_id))


def _validate_tenant_id(tenant_id: str) -> str:
    normalized = tenant_id.strip()
    if not _TENANT_ID_PATTERN.fullmatch(normalized):
        raise ValueError("tenant_id is not safe for knowledge source path resolution")
    return normalized


def _scan_markdown_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*.md"):
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("vault path escaped configured root")
        files.append(resolved)
    return sorted(files)
