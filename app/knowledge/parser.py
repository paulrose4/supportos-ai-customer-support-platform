import json
import re
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import frontmatter

from app.knowledge.models import ParsedKnowledgeDocument

_REQUIRED_FIELDS = frozenset(
    {
        "document_id",
        "tenant_id",
        "title",
        "category",
        "audience",
        "product",
        "region",
        "language",
        "status",
        "authority_level",
        "priority",
        "version",
        "effective_from",
        "effective_to",
        "owner_role",
        "reviewer",
        "updated_at",
    }
)
_ALLOWED_STATUSES = frozenset({"draft", "review", "published", "archived"})
_HIGH_RISK_CATEGORIES = frozenset(
    {"refund", "payment", "privacy", "legal", "compensation", "account_security"}
)
_CARE_SOP_CATEGORY = "product_care_sop"
_GENERAL_CARE_CATEGORY = "product_care_general"
_CARE_PROCEDURE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
_CARE_MATERIALS = frozenset({"tpe", "silicone", "pvc", "tpe_silicone"})
_INTERNAL_LINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_PROMPT_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "reveal the system prompt",
    "忽略之前的指令",
    "忽略以上指令",
    "泄露系统提示词",
)


def _locale_base(value: object) -> str:
    return str(value or "").strip().replace("_", "-").casefold().split("-", 1)[0]


def _declared_care_locales(metadata: dict[str, Any]) -> tuple[str, ...]:
    raw = metadata.get("care_locales")
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ValueError("care_locales must be a non-empty list")
    locales = tuple(dict.fromkeys(_locale_base(value) for value in raw if _locale_base(value)))
    if not locales or len(locales) != len(raw):
        raise ValueError("care_locales must contain valid language tags")
    return locales


def _has_localized_values(values: object, locales: tuple[str, ...]) -> bool:
    if not isinstance(values, dict):
        return False
    available = {_locale_base(key) for key, value in values.items() if str(value or "").strip()}
    return all(locale in available for locale in locales)


class MarkdownKnowledgeParser:
    def parse(self, path: Path) -> ParsedKnowledgeDocument:
        post = frontmatter.load(path)
        metadata = {key: _json_safe(value) for key, value in post.metadata.items()}
        missing = sorted(_REQUIRED_FIELDS.difference(metadata))
        if missing:
            raise ValueError(f"missing required frontmatter fields: {', '.join(missing)}")

        status = str(metadata["status"]).casefold()
        if status not in _ALLOWED_STATUSES:
            raise ValueError(f"unsupported knowledge status: {status}")
        metadata["status"] = status
        metadata["authority_level"] = int(metadata["authority_level"])
        metadata["priority"] = int(metadata["priority"])
        metadata["allow_translation"] = bool(metadata.get("allow_translation", False))

        category = str(metadata["category"]).casefold()
        if status == "published" and category in _HIGH_RISK_CATEGORIES:
            if not str(metadata.get("reviewer") or "").strip():
                raise ValueError("published high-risk knowledge requires a reviewer")
        if category == _CARE_SOP_CATEGORY:
            _validate_care_sop_metadata(metadata)
        if category == _GENERAL_CARE_CATEGORY:
            _validate_general_care_metadata(metadata)

        body = post.content.strip()
        normalized_body = body.casefold()
        if any(pattern in normalized_body for pattern in _PROMPT_INJECTION_PATTERNS):
            raise ValueError("knowledge content contains a prompt-injection pattern")
        internal_links = tuple(
            dict.fromkeys(match.strip() for match in _INTERNAL_LINK_PATTERN.findall(body))
        )
        hash_input = json.dumps(
            {"metadata": metadata, "body": body},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return ParsedKnowledgeDocument(
            path=path,
            metadata=metadata,
            body=body,
            internal_links=internal_links,
            content_hash=sha256(hash_input.encode("utf-8")).hexdigest(),
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    return value


def _validate_care_sop_metadata(metadata: dict[str, Any]) -> None:
    _validate_company_care_pack_metadata(metadata)
    procedure_id = str(metadata.get("procedure_id") or "").strip()
    if not _CARE_PROCEDURE_ID_PATTERN.fullmatch(procedure_id):
        raise ValueError("care SOP requires a valid procedure_id")
    if metadata.get("approval_status") not in {"pending_review", "approved", "retired"}:
        raise ValueError("care SOP requires a supported approval_status")
    if metadata["status"] == "published":
        if metadata.get("approval_status") != "approved":
            raise ValueError("published care SOP requires approved status")
        if int(metadata["authority_level"]) < 80:
            raise ValueError("published care SOP requires authority_level >= 80")
        if not str(metadata.get("reviewed_at") or "").strip():
            raise ValueError("published care SOP requires reviewed_at")
        if str(metadata.get("reviewer") or "").strip() in {
            "",
            "automated_web_ingestion",
            "unassigned",
        }:
            raise ValueError("published care SOP requires a named human reviewer")
        approval_references = metadata.get("approval_references")
        if (
            not isinstance(approval_references, list)
            or not approval_references
            or not all(str(value).strip() for value in approval_references)
        ):
            raise ValueError("published care SOP requires approval_references")

    materials = metadata.get("applicable_materials")
    if not isinstance(materials, list) or not materials:
        raise ValueError("care SOP requires applicable_materials")
    normalized_materials = {str(value).casefold() for value in materials}
    if not normalized_materials.issubset(_CARE_MATERIALS):
        raise ValueError("care SOP contains an unsupported material")

    prohibited_actions = metadata.get("prohibited_actions")
    if not isinstance(prohibited_actions, list) or not prohibited_actions:
        raise ValueError("care SOP requires prohibited_actions")

    steps = metadata.get("approved_steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("care SOP requires approved_steps")
    seen_step_ids: set[str] = set()
    care_locales = _declared_care_locales(metadata)
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("care SOP steps must be objects")
        step_id = str(step.get("step_id") or "").strip()
        if not _CARE_PROCEDURE_ID_PATTERN.fullmatch(step_id) or step_id in seen_step_ids:
            raise ValueError("care SOP step_id must be valid and unique")
        seen_step_ids.add(step_id)
        instructions = step.get("instructions")
        if not isinstance(instructions, dict):
            raise ValueError("care SOP steps require localized instructions")
        required_locales = care_locales or ("en", "zh")
        if not _has_localized_values(instructions, required_locales):
            raise ValueError("care SOP steps are missing a reviewed locale instruction")
        if (
            metadata["status"] == "published"
            and care_locales
            and not _has_localized_values(instructions, care_locales)
        ):
            raise ValueError("published care SOP is missing a declared locale instruction")


def _validate_general_care_metadata(metadata: dict[str, Any]) -> None:
    _validate_company_care_pack_metadata(metadata)
    if metadata.get("approval_status") not in {"pending_review", "approved", "retired"}:
        raise ValueError("general care knowledge requires a supported approval_status")
    if metadata.get("guidance_scope") != "universal_low_risk":
        raise ValueError("general care knowledge requires universal_low_risk scope")
    prohibited_actions = metadata.get("prohibited_actions")
    if not isinstance(prohibited_actions, list) or not prohibited_actions:
        raise ValueError("general care knowledge requires prohibited_actions")
    approved_responses = metadata.get("approved_responses")
    care_locales = _declared_care_locales(metadata)
    required_locales = care_locales or ("en", "zh")
    if not _has_localized_values(approved_responses, required_locales):
        raise ValueError("general care knowledge requires reviewed locale responses")
    if metadata["status"] != "published":
        return
    if care_locales and not _has_localized_values(approved_responses, care_locales):
        raise ValueError("published general care is missing a declared locale response")
    if metadata.get("approval_status") != "approved":
        raise ValueError("published general care knowledge requires approved status")
    if int(metadata["authority_level"]) < 80:
        raise ValueError("published general care knowledge requires authority_level >= 80")
    if not str(metadata.get("reviewed_at") or "").strip():
        raise ValueError("published general care knowledge requires reviewed_at")
    if str(metadata.get("reviewer") or "").strip() in {
        "",
        "automated_web_ingestion",
        "unassigned",
    }:
        raise ValueError("published general care knowledge requires a named human reviewer")
    approval_references = metadata.get("approval_references")
    if (
        not isinstance(approval_references, list)
        or not approval_references
        or not all(str(value).strip() for value in approval_references)
    ):
        raise ValueError("published general care knowledge requires approval_references")


def _validate_company_care_pack_metadata(metadata: dict[str, Any]) -> None:
    """Keep published care content tied to the shared company care package."""
    if metadata.get("status") != "published":
        return
    if metadata.get("tenant_id") != "__global__":
        raise ValueError("published care knowledge must use the __global__ tenant")
    care_pack_id = str(metadata.get("care_pack_id") or "").strip()
    if not care_pack_id:
        raise ValueError("published care knowledge requires care_pack_id")
    care_pack_version = str(metadata.get("care_pack_version") or "").strip()
    if not care_pack_version:
        raise ValueError("published care knowledge requires care_pack_version")
    if metadata.get("care_scope") != "company_global":
        raise ValueError("published care knowledge requires company_global care_scope")
