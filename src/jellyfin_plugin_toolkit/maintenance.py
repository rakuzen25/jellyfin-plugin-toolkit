"""Maintenance entrypoints that remain usable when consumer bindings are stale."""

import argparse
import json
import os
import sys
from pathlib import Path

from jellyfin_plugin_toolkit import bindings


def paths(root):
    python = Path(sys.executable)
    return {
        "toolkit": str(root / ".build/jpt"),
        "python": str(python),
        "ruff": str(python.with_name("ruff")),
        "eslint": str(root / "node_modules/.bin/eslint"),
        "typescript": str(root / "node_modules/typescript/lib"),
    }


def editor(root, write=False):
    selected = paths(root)
    path = bindings.safe_path(root, ".vscode/settings.json")
    original = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(original, dict):
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


def main(argv=None):
    forwarded = list(sys.argv[1:] if argv is None else argv)
    if forwarded and forwarded[0] == "sync":
        return bindings.main(forwarded[1:])
    parser = argparse.ArgumentParser(prog="jpt")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("paths")
    setup = sub.add_parser("editor")
    setup.add_argument("editor", choices=["vscode"])
    setup.add_argument("--apply", action="store_true")
    args = parser.parse_args(forwarded)
    root = Path(os.environ.get("JELLYFIN_PLUGIN_ROOT", ".")).resolve()
    if args.command == "paths":
        print(json.dumps(paths(root), indent=2))
    else:
        try:
            editor(root, args.apply)
        except (ValueError, OSError) as error:
            parser.exit(
                1, f"Editor setup failed: {error}. Existing settings were preserved.\n"
            )
    return 0
