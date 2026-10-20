"""The layout and the licence rules the component must hold."""

from __future__ import annotations

import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "room_climate"


def test_domain_matches_directory() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == COMPONENT.name
    assert manifest["version"]
    assert manifest["requirements"] == ["pydantic>=2.11,<3"]


def test_control_module_has_no_home_assistant_import() -> None:
    """control.py is host independent, so its rules stay unit testable."""
    tree = ast.parse((COMPONENT / "control.py").read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name.startswith("homeassistant")]


def test_repository_carries_a_licence() -> None:
    assert (ROOT / "LICENSE").read_text().strip()
