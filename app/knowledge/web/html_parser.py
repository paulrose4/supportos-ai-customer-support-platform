import json
import re
from typing import Any
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser, Node

from app.knowledge.web.canonicalizer import canonicalize_url
from app.knowledge.web.models import HtmlContentBlock, ParsedHtmlPage

_CONTENT_ROOT_SELECTORS = (
    "#article-content",
    ".markdown-body",
    '[itemprop="articleBody"]',
    "main article",
    "article",
    "main",
    '[role="main"]',
    ".product-detail",
    ".product-info",
    ".product-description",
    "body",
)
_REMOVE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "header",
    "footer",
    "nav",
    "aside",
    "button",
    "input",
    "select",
    "textarea",
    '[aria-hidden="true"]',
    "[hidden]",
    ".related",
    ".recommend",
    ".recommendation",
    ".newsletter",
    ".modal",
    ".share",
    ".sidebar",
    ".social",
    ".toolbar",
    ".menu",
    '[class*="menu"]',
    '[id*="menu"]',
    ".related-products",
    ".cross-sells",
    ".upsells",
    '[class*="recommend"]',
    '[id*="recommend"]',
)
_BLOCK_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li",
        "dt",
        "dd",
        "blockquote",
        "figcaption",
        "pre",
        "tr",
    }
)
_WHITESPACE = re.compile(r"\s+")


class HtmlKnowledgeParser:
    def parse(
        self,
        *,
        requested_url: str,
        final_url: str,
        body: bytes,
        content_type: str,
        allowed_hosts: frozenset[str],
    ) -> ParsedHtmlPage:
        text = _decode_html(body, content_type)
        tree = HTMLParser(text)
        meta = _meta(tree)
        structured_data = _structured_data(tree)
        title, title_source = _title(tree, meta, structured_data)
        canonical_url = _canonical_url(tree, final_url, allowed_hosts)
        language = _language(tree)
        internal_links = _internal_links(tree, final_url, allowed_hosts)
        _remove_boilerplate(tree)
        selected_root, selected_root_label = _select_content_root(tree)
        blocks = _extract_blocks(selected_root)
        selected_text = _clean_text(selected_root.text(separator=" ", strip=True))
        return ParsedHtmlPage(
            requested_url=requested_url,
            final_url=final_url,
            canonical_url=canonical_url,
            title=title[:1000],
            language=language,
            meta=meta,
            blocks=tuple(blocks),
            internal_links=internal_links,
            structured_data=structured_data,
            selected_root=selected_root_label,
            selected_text_characters=len(selected_text),
            title_source=title_source,
            content_kind=_content_kind(
                final_url,
                selected_root_label,
                structured_data,
                block_count=len(blocks),
                selected_text_characters=len(selected_text),
            ),
        )


def _meta(tree: HTMLParser) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in tree.css("meta"):
        key = (
            node.attributes.get("name")
            or node.attributes.get("property")
            or node.attributes.get("itemprop")
            or ""
        ).strip()
        content = (node.attributes.get("content") or "").strip()
        if key and content:
            result[key.casefold()] = content[:5000]
    return result


def _structured_data(tree: HTMLParser) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for node in tree.css('script[type*="ld+json"]'):
        raw = node.text(strip=True)
        if not raw or len(raw) > 1_000_000:
            continue
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(value, dict):
            result.append(value)
        elif isinstance(value, list):
            result.extend(item for item in value if isinstance(item, dict))
    return tuple(result)


def _title(
    tree: HTMLParser,
    meta: dict[str, str],
    structured_data: tuple[dict[str, Any], ...],
) -> tuple[str, str]:
    title_node = tree.css_first("title")
    candidates = (
        ("title", "" if title_node is None else title_node.text(strip=True)),
        ("og:title", meta.get("og:title", "")),
        ("twitter:title", meta.get("twitter:title", "")),
        ("json_ld", _json_ld_title(structured_data)),
        ("h1", _first_text(tree, "h1")),
    )
    for source, value in candidates:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned, source
    return "", "none"


def _json_ld_title(values: tuple[dict[str, Any], ...]) -> str:
    for item in _walk_json_ld(values):
        for key in ("headline", "name"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _walk_json_ld(values: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = list(values)
    while queue:
        item = queue.pop(0)
        result.append(item)
        graph = item.get("@graph")
        if isinstance(graph, list):
            queue.extend(value for value in graph if isinstance(value, dict))
    return result


def _canonical_url(
    tree: HTMLParser,
    final_url: str,
    allowed_hosts: frozenset[str],
) -> str:
    node = tree.css_first('link[rel~="canonical"]')
    if node is None or not node.attributes.get("href"):
        return final_url
    try:
        return canonicalize_url(
            node.attributes["href"],
            base_url=final_url,
            allowed_hosts=allowed_hosts,
        )
    except ValueError:
        return final_url


def _language(tree: HTMLParser) -> str | None:
    node = tree.css_first("html")
    if node is None:
        return None
    return node.attributes.get("lang") or None


def _internal_links(
    tree: HTMLParser,
    final_url: str,
    allowed_hosts: frozenset[str],
) -> tuple[str, ...]:
    result: set[str] = set()
    for node in tree.css("a[href]"):
        try:
            result.add(
                canonicalize_url(
                    node.attributes["href"],
                    base_url=final_url,
                    allowed_hosts=allowed_hosts,
                )
            )
        except ValueError:
            continue
    return tuple(sorted(result))


def _remove_boilerplate(tree: HTMLParser) -> None:
    for selector in _REMOVE_SELECTORS:
        for node in tree.css(selector):
            node.decompose()


def _select_content_root(tree: HTMLParser) -> tuple[Node, str]:
    for selector in _CONTENT_ROOT_SELECTORS:
        node = tree.css_first(selector)
        if node is None:
            continue
        if len(_clean_text(node.text(separator=" ", strip=True))) >= 80:
            return node, selector
    body = tree.css_first("body")
    if body is not None:
        return body, "body_fallback"
    return tree.root, "document_fallback"


def _extract_blocks(root: Node) -> list[HtmlContentBlock]:
    blocks: list[HtmlContentBlock] = []
    seen: set[tuple[str, str]] = set()
    for node in root.traverse():
        tag = node.tag.casefold() if node.tag else ""
        if tag not in _BLOCK_TAGS:
            continue
        if tag == "tr":
            cells = [
                _clean_text(cell.text(separator=" ", strip=True)) for cell in node.css("th, td")
            ]
            text = " | ".join(cell for cell in cells if cell)
            kind = "table_row"
            heading_level = None
        else:
            text = _clean_text(node.text(separator=" ", strip=True))
            kind = (
                "heading"
                if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit()
                else "list_item"
                if tag in {"li", "dt", "dd"}
                else "paragraph"
            )
            heading_level = int(tag[1]) if kind == "heading" else None
        key = (kind, text.casefold())
        if not text or key in seen:
            continue
        seen.add(key)
        blocks.append(HtmlContentBlock(kind, text, heading_level, True))
    if blocks:
        return blocks
    text = _clean_text(root.text(separator=" ", strip=True))
    return [HtmlContentBlock("text", text, None, True)] if len(text) >= 20 else []


def _content_kind(
    final_url: str,
    selected_root: str,
    structured_data: tuple[dict[str, Any], ...],
    *,
    block_count: int,
    selected_text_characters: int,
) -> str:
    types = {str(item.get("@type") or "").casefold() for item in _walk_json_ld(structured_data)}
    if types & {"product", "productgroup"}:
        return "product"
    if "collectionpage" in types:
        return "category"
    path = urlsplit(final_url).path.casefold()
    if path.startswith("/guides/"):
        has_article_evidence = bool(
            types & {"article", "blogposting", "howto", "newsarticle", "techarticle"}
        ) or selected_root in {
            "#article-content",
            ".markdown-body",
            '[itemprop="articleBody"]',
        }
        if has_article_evidence or (block_count >= 6 and selected_text_characters >= 800):
            return "guide"
        return "category"
    if selected_root in {
        "#article-content",
        ".markdown-body",
        '[itemprop="articleBody"]',
        "main article",
        "article",
    }:
        return "guide"
    return "general"


def _first_text(tree: HTMLParser, selector: str) -> str:
    for node in tree.css(selector):
        value = _clean_text(node.text(separator=" ", strip=True))
        if value:
            return value
    return ""


def _clean_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _decode_html(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, re.IGNORECASE)
    candidates = [match.group(1)] if match else []
    candidates.extend(["utf-8", "windows-1252"])
    for encoding in candidates:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")
