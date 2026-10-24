"""Maintenance entrypoints that remain usable when consumer bindings are stale."""

import json
import sys
from pathlib import Path
from typing import Any

from jellyfin_plugin_toolkit import bindings
from jellyfin_plugin_toolkit.common.json_shapes import is_json_object


def paths(root: Path) -> dict[str, str]:
    python = Path(sys.executable)
    return {
        "toolkit": str(root / ".build/jpt"),
        "python": str(python),
        "ruff": str(python.with_name("ruff")),
        "mypy": str(python.with_name("mypy")),
        "basedpyright": str(python.with_name("basedpyright")),
        "eslint": str(root / "node_modules/.bin/eslint"),
        "typescript": str(root / "node_modules/typescript/lib"),
    }


def editor(root: Path, write: bool = False) -> None:
    selected = paths(root)
    path = bindings.safe_path(root, ".vscode/settings.json")
    original: Any = json.loads(path.read_text()) if path.exists() else {}
    if not is_json_object(original):
        raise ValueError(
            "VS Code settings must be a JSON object; use jpt paths for manual setup"
        )
    merged = {
        **original,
        "python.defaultInterpreterPath": selected["python"],
        "ruff.path": [selected["ruff"]],
        "typescript.tsdk": selected["typescript"],
    }
    change = bindings.change_for(
        root, ".vscode/settings.json", (json.dumps(merged, indent=4) + "\n").encode()
    )
    changes = [change] if change else []
    bindings.preview(changes)
    if write and changes:
        path.parent.mkdir(exist_ok=True)
        bindings.apply(root, changes)
