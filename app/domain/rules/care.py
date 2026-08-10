import json
import re
from collections.abc import Iterable
from urllib.parse import urlparse

from app.domain.models.care import (
    ApprovedCareProcedure,
    ApprovedCareStep,
    CareGuidanceDecision,
    CareRiskTier,
)
from app.domain.models.evidence import KnowledgeEvidence

_CARE_TERMS = (
    "care",
    "clean",
    "wash",
    "maintain",
    "maintenance",
    "storage",
    "store it",
    "drying",
    "powder",
    "lubric",
    "disinfect",
    "保养",
    "清洁",
    "清洗",
    "维护",
    "存放",
    "收纳",
    "晾干",
    "护理粉",
    "润滑",
    "消毒",
    "お手入れ",
    "洗浄",
    "保管",
    "pflege",
    "reinigen",
    "aufbewahrung",
    "limpiar",
    "cuidado",
    "entretien",
    "nettoyer",
)
_HIGH_RISK_TERMS = (
    "repair",
    "tear",
    "torn",
    "crack",
    "mold",
    "mould",
    "severe stain",
    "broken",
    "fault",
    "拆修",
    "修复",
    "破损",
    "撕裂",
    "开裂",
    "霉",
    "严重染色",
    "坏",
    "故障",
    "reparier",
    "riss",
    "schimmel",
    "réparer",
    "moisissure",
)
_MATERIAL_DEPENDENT_TERMS = (
    "clean",
    "wash",
    "soap",
    "cleaner",
    "powder",
    "oil",
    "alcohol",
    "bleach",
    "disinfect",
    "lubric",
    "soak",
    "water",
    "temperature",
    "heat",
    "保养",
    "清洁",
    "清洗",
    "清洁剂",
    "肥皂",
    "护理粉",
    "油",
    "酒精",
    "漂白",
    "消毒",
    "润滑",
    "浸泡",
    "温度",
    "加热",
)
_SPECIAL_FEATURE_TERMS = (
    "heating",
    "heater",
    "electronic",
    "motor",
    "voice",
    "app control",
    "removable insert",
    "加热",
    "电子",
    "电机",
    "语音",
    "可拆卸",
)
_MATERIAL_ALIASES = {
    "tpe": ("tpe", "thermoplastic elastomer", "热塑性弹性体"),
    "silicone": ("silicone", "硅胶", "シリコン", "silikon"),
    "pvc": ("pvc", "polyvinyl chloride", "聚氯乙烯"),
}
_IDENTIFIER_PATTERN = re.compile(
    r"\b(?=[A-Za-z0-9_-]{4,}\b)(?=[A-Za-z0-9_-]*[A-Za-z])"
    r"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"
)
_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_PROCEDURE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
_GENERAL_CARE_CATEGORY = "product_care_general"


def normalize_care_locale(value: str | None) -> str:
    """Return a stable BCP-47-like locale key for care content selection."""
    normalized = "-".join(str(value or "").strip().replace("_", "-").split())
    if not normalized:
        return ""
    parts = normalized.split("-")
    language = parts[0].casefold()
    if not language.isalpha() or not 2 <= len(language) <= 8:
        return ""
    result = [language]
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            result.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            result.append(part.title())
        elif part.isalnum():
            result.append(part.casefold())
    return "-".join(result)


def care_locale_candidates(value: str | None) -> tuple[str, ...]:
    """Return exact locale then base language, without an unsafe language fallback."""
    normalized = normalize_care_locale(value)
    if not normalized:
        return ()
    base = normalized.split("-", 1)[0]
    return (normalized,) if normalized == base else (normalized, base)


def localized_care_text(values: object, response_language: str | None) -> str | None:
    """Select only an explicitly reviewed locale or base-language care translation."""
    if not isinstance(values, dict):
        return None
    for locale in care_locale_candidates(response_language):
        for key, value in values.items():
            if normalize_care_locale(str(key)) == locale and str(value or "").strip():
                return str(value).strip()
    return None


def classify_care_risk(message: str) -> CareRiskTier | None:
    normalized = " ".join(message.casefold().split())
    if any(term in normalized for term in _HIGH_RISK_TERMS):
        return CareRiskTier.HIGH
    if not any(term in normalized for term in _CARE_TERMS):
        return None
    if any(term in normalized for term in _MATERIAL_DEPENDENT_TERMS):
        return CareRiskTier.MATERIAL_DEPENDENT
    return CareRiskTier.LOW


def evaluate_care_guidance(
    *,
    message: str,
    response_language: str,
    page_path: str | None,
    evidence: list[KnowledgeEvidence],
) -> CareGuidanceDecision:
    risk_tier = classify_care_risk(message)
    if risk_tier is None:
        return CareGuidanceDecision(status="not_care")
    if risk_tier is CareRiskTier.HIGH:
        return CareGuidanceDecision(
            status="handoff",
            risk_tier=risk_tier,
            reason_code="care_high_risk",
        )

    product_evidence = _matching_product_evidence(message, page_path, evidence)
    if any(_contains_special_feature(item) for item in product_evidence):
        return CareGuidanceDecision(
            status="handoff",
            risk_tier=CareRiskTier.HIGH,
            product_source=product_evidence[0].source,
            reason_code="care_special_feature",
        )

    material = _material_from_evidence(product_evidence)
    if material is None:
        if any(
            is_approved_general_care_evidence(item, response_language=response_language)
            for item in evidence
        ):
            return CareGuidanceDecision(
                status="general",
                risk_tier=risk_tier,
                reason_code="care_general_guidance",
            )
        if any(is_approved_general_care_evidence(item) for item in evidence):
            return CareGuidanceDecision(
                status="handoff",
                risk_tier=risk_tier,
                reason_code="care_language_missing",
            )
        return CareGuidanceDecision(
            status="clarification",
            risk_tier=risk_tier,
            reason_code="care_product_unidentified",
        )

    procedure = _approved_procedure(
        evidence=evidence,
        material=material,
        response_language=response_language,
    )
    product_source = product_evidence[0].source if product_evidence else None
    if procedure is None:
        if any(
            is_approved_general_care_evidence(item, response_language=response_language)
            for item in evidence
        ):
            return CareGuidanceDecision(
                status="general",
                risk_tier=risk_tier,
                material=material,
                product_source=product_source,
                reason_code="care_material_sop_missing",
            )
        if any(is_approved_general_care_evidence(item) for item in evidence):
            return CareGuidanceDecision(
                status="handoff",
                risk_tier=risk_tier,
                material=material,
                product_source=product_source,
                reason_code="care_language_missing",
            )
        return CareGuidanceDecision(
            status="handoff",
            risk_tier=risk_tier,
            material=material,
            product_source=product_source,
            reason_code="care_sop_missing",
        )
    return CareGuidanceDecision(
        status="approved",
        risk_tier=risk_tier,
        material=material,
        procedure=procedure,
        product_source=product_source,
    )


def is_approved_general_care_evidence(
    evidence: KnowledgeEvidence,
    *,
    response_language: str | None = None,
) -> bool:
    metadata = evidence.metadata
    if metadata.get("tenant_id") != "__global__":
        return False
    if metadata.get("category") != _GENERAL_CARE_CATEGORY:
        return False
    if metadata.get("status") != "published":
        return False
    if metadata.get("approval_status") != "approved":
        return False
    if metadata.get("guidance_scope") != "universal_low_risk":
        return False
    if int(metadata.get("authority_level", 0)) < 80:
        return False
    if not str(metadata.get("reviewed_at") or "").strip():
        return False
    if str(metadata.get("reviewer") or "").strip() in {
        "",
        "automated_web_ingestion",
        "unassigned",
    }:
        return False
    approval_references = metadata.get("approval_references")
    approved_responses = metadata.get("approved_responses")
    declared_locales = metadata.get("care_locales")
    required_locales = (
        tuple(str(value) for value in declared_locales)
        if isinstance(declared_locales, (list, tuple, set)) and declared_locales
        else ("en", "zh")
    )
    valid = (
        isinstance(approval_references, list)
        and bool(approval_references)
        and all(str(value).strip() for value in approval_references)
        and isinstance(approved_responses, dict)
        and all(localized_care_text(approved_responses, language) for language in required_locales)
    )
    if not valid:
        return False
    if response_language is None:
        return True
    return localized_care_text(approved_responses, response_language) is not None


def _matching_product_evidence(
    message: str,
    page_path: str | None,
    evidence: list[KnowledgeEvidence],
) -> list[KnowledgeEvidence]:
    candidates = [item for item in evidence if item.metadata.get("category") != "product_care_sop"]
    normalized_path = (page_path or "").strip()
    if normalized_path and normalized_path != "/":
        page_matches = [item for item in candidates if normalized_path in _evidence_paths(item)]
        if page_matches:
            return page_matches

    linked_paths = {_source_path(url.rstrip(".,，。)）")) for url in _URL_PATTERN.findall(message)}
    if linked_paths:
        link_matches = [item for item in candidates if _evidence_paths(item) & linked_paths]
        if link_matches:
            return link_matches

    identifiers = {match.casefold() for match in _IDENTIFIER_PATTERN.findall(message)}
    if identifiers:
        identifier_matches = [
            item
            for item in candidates
            if identifiers & set(_IDENTIFIER_PATTERN.findall(_evidence_text(item).casefold()))
        ]
        if identifier_matches:
            return identifier_matches
    return []


def _material_from_evidence(evidence: list[KnowledgeEvidence]) -> str | None:
    detected = {
        material for item in evidence for material in _materials_in_text(_evidence_text(item))
    }
    if detected == {"tpe", "silicone"}:
        return "tpe_silicone"
    return next(iter(detected)) if len(detected) == 1 else None


def _evidence_paths(evidence: KnowledgeEvidence) -> set[str]:
    sources = {
        evidence.source,
        str(evidence.metadata.get("canonical_url") or ""),
        str(evidence.metadata.get("requested_url") or ""),
        str(evidence.metadata.get("final_url") or ""),
    }
    return {_source_path(source) for source in sources if source.strip()}


def _approved_procedure(
    *,
    evidence: list[KnowledgeEvidence],
    material: str,
    response_language: str,
) -> ApprovedCareProcedure | None:
    candidates: list[tuple[int, int, float, ApprovedCareProcedure]] = []
    requested_locales = care_locale_candidates(response_language)
    for item in evidence:
        metadata = item.metadata
        if metadata.get("category") != "product_care_sop":
            continue
        if metadata.get("status") != "published":
            continue
        if metadata.get("approval_status") != "approved":
            continue
        if int(metadata.get("authority_level", 0)) < 80:
            continue
        if not str(metadata.get("reviewed_at") or "").strip():
            continue
        if str(metadata.get("reviewer") or "").strip() in {
            "",
            "automated_web_ingestion",
            "unassigned",
        }:
            continue
        approval_references = metadata.get("approval_references")
        if (
            not isinstance(approval_references, list)
            or not approval_references
            or not all(str(value).strip() for value in approval_references)
        ):
            continue
        if metadata.get("tenant_id") != "__global__":
            continue
        materials = {str(value).casefold() for value in metadata.get("applicable_materials", [])}
        if material not in materials:
            continue
        locale_rank = _care_locale_rank(metadata, requested_locales)
        if locale_rank is None:
            continue
        procedure = _procedure_from_evidence(item, material, response_language)
        if procedure is not None:
            candidates.append(
                (locale_rank, int(metadata.get("authority_level", 0)), item.score, procedure)
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return candidates[0][3]


def _care_locale_rank(metadata: dict[str, object], requested: tuple[str, ...]) -> int | None:
    """Return a deterministic preference for exact locale over base language."""
    if not requested:
        return None
    declared = metadata.get("care_locales")
    if isinstance(declared, (list, tuple, set)) and declared:
        available = {
            normalize_care_locale(str(value))
            for value in declared
            if normalize_care_locale(str(value))
        }
        for index, locale in enumerate(requested):
            if locale in available:
                return len(requested) - index
    return len(requested) if _procedure_has_locale(metadata, requested) else None


def _procedure_has_locale(metadata: dict[str, object], requested: tuple[str, ...]) -> bool:
    for raw_step in metadata.get("approved_steps", []):
        if not isinstance(raw_step, dict):
            return False
        instructions = raw_step.get("instructions")
        if not isinstance(instructions, dict):
            return False
        if localized_care_text(instructions, requested[0]) is not None:
            continue
        if len(requested) > 1 and localized_care_text(instructions, requested[1]) is not None:
            continue
        return False
    return bool(metadata.get("approved_steps"))


def _procedure_from_evidence(
    evidence: KnowledgeEvidence,
    material: str,
    language: str,
) -> ApprovedCareProcedure | None:
    metadata = evidence.metadata
    procedure_id = str(metadata.get("procedure_id") or "").strip()
    if not _PROCEDURE_ID_PATTERN.fullmatch(procedure_id):
        return None
    prohibited_actions = metadata.get("prohibited_actions")
    if not isinstance(prohibited_actions, list) or not prohibited_actions:
        return None
    steps: list[ApprovedCareStep] = []
    for raw_step in metadata.get("approved_steps", []):
        if not isinstance(raw_step, dict):
            return None
        instructions = raw_step.get("instructions")
        if not isinstance(instructions, dict):
            return None
        instruction = localized_care_text(instructions, language)
        if not instruction or "REVIEW REQUIRED" in instruction or "需要供应商审核" in instruction:
            return None
        step_id = str(raw_step.get("step_id") or "").strip()
        if not step_id:
            return None
        steps.append(ApprovedCareStep(step_id=step_id, instruction=instruction))
    if not steps:
        return None
    return ApprovedCareProcedure(
        procedure_id=procedure_id,
        material=material,
        steps=tuple(steps),
        prohibited_actions=tuple(str(value) for value in prohibited_actions),
        source=evidence.source,
        chunk_id=evidence.chunk_id,
        version=str(metadata.get("version") or ""),
    )


def _contains_special_feature(evidence: KnowledgeEvidence) -> bool:
    normalized = _evidence_text(evidence).casefold()
    return any(term in normalized for term in _SPECIAL_FEATURE_TERMS)


def _materials_in_text(text: str) -> Iterable[str]:
    normalized = text.casefold()
    for material, aliases in _MATERIAL_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            yield material


def _evidence_text(evidence: KnowledgeEvidence) -> str:
    product = evidence.metadata.get("product") or {}
    return f"{evidence.text}\n{json.dumps(product, ensure_ascii=False, sort_keys=True)}"


def _source_path(source: str) -> str:
    parsed = urlparse(source)
    return parsed.path or "/"
