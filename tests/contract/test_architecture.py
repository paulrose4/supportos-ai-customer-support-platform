import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

LAYER_RULES = {
    "app/domain": {
        "fastapi",
        "langgraph",
        "sqlalchemy",
        "qdrant_client",
        "openai",
        "app.api",
        "app.graphs",
        "app.integrations",
    },
    "app/application": {
        "fastapi",
        "langgraph",
        "sqlalchemy",
        "qdrant_client",
        "openai",
        "app.api",
        "app.graphs",
        "app.integrations",
    },
    "app/api": {"app.integrations"},
    "app/graphs": {"app.integrations"},
    "app/tools": {"app.integrations"},
    "app/knowledge/web": {
        "fastapi",
        "langgraph",
        "sqlalchemy",
        "qdrant_client",
        "openai",
        "app.integrations",
    },
}


@pytest.mark.parametrize("layer,forbidden", LAYER_RULES.items())
def test_layer_import_boundaries(layer: str, forbidden: set[str]) -> None:
    violations: list[str] = []
    for path in sorted((ROOT / layer).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imports = _imports(node)
            for imported in imports:
                if any(
                    imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden
                ):
                    violations.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert not violations, "\n".join(violations)


def _imports(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return (node.module,)
    return ()
