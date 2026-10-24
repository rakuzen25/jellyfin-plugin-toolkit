"""Validate workflows in checkouts, generated projects, and source distributions."""

from collections.abc import Sequence
from pathlib import Path

from jellyfin_plugin_toolkit.common.process import run
from jellyfin_plugin_toolkit.context import Context


def check_workflows(
    root: Path, image: str | None = None, ignores: Sequence[str] = ()
) -> None:
    if image is None:
        image = Context.load(root).config.images["actionlint"]
    paths = sorted(
        str(path.relative_to(root))
        for suffix in ["*.yml", "*.yaml"]
        for path in (root / ".github/workflows").glob(suffix)
    )
    if not paths:
        raise ValueError("No maintained workflows found")
    # Explicit files do not require actionlint to discover a .git directory.
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        str(root) + ":/work:ro",
        "-w",
        "/work",
        image,
        *[argument for pattern in ignores for argument in ("-ignore", pattern)],
        *paths,
        cwd=root,
    )
