"""Run remark's repository-wide link checks with toolkit navigation policy."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context

import json
import os
import subprocess
from pathlib import Path

from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT


def documents(root: Path) -> list[Path]:
    return [
        path
        for path in [
            *sorted(root.glob("*.md")),
            *sorted((root / "docs").rglob("*.md")),
            *sorted((root / "vendor").glob("*.md")),
        ]
        if path.is_file()
    ]


def check(root: Path, *, dependency_root: Path | None = None) -> list[str]:
    root = root.resolve()
    result = subprocess.run(
        [
            "node",
            str(TOOLKIT_ROOT / "node/check-docs.mjs"),
            str(root),
            *[str(path) for path in documents(root)],
        ],
        check=True,
        text=True,
        capture_output=True,
        env={**os.environ, "JELLYFIN_PLUGIN_ROOT": str(dependency_root or root)},
    )
    errors: list[str] = json.loads(result.stdout)
    return errors


def main(ctx: Context) -> None:
    errors = check(ctx.root)
    if errors:
        raise ValueError("\n".join(errors))
    print(
        f"PASS: local links, anchors, and README reachability ({len(documents(ctx.root))} Markdown files)."
    )
