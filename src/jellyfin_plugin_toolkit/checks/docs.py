"""Run remark's repository-wide link checks with toolkit navigation policy."""

import json
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


def check(root: Path) -> list[str]:
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
    )
    return json.loads(result.stdout)


def main(ctx) -> None:
    errors = check(ctx.root)
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"PASS: local links, anchors, and README reachability ({len(documents(ctx.root))} Markdown files)."
    )


if __name__ == "__main__":
    from jellyfin_plugin_toolkit.context import Context

    main(Context.load())
