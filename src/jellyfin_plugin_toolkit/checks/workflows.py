"""Validate workflows in checkouts, generated projects, and source distributions."""

from collections.abc import Sequence
from pathlib import Path

from jellyfin_plugin_toolkit.common.process import run
from jellyfin_plugin_toolkit.validators import image


def check_workflows(root: Path, ignores: Sequence[str] = ()) -> None:
    paths = sorted(
        str(path.relative_to(root))
        for suffix in ["*.yml", "*.yaml"]
        for path in (root / ".github/workflows").glob(suffix)
    )
    if not paths:
        raise ValueError("No maintained workflows found")
    configurations = [
        path
        for path in (".github/actionlint.yaml", ".github/actionlint.yml")
        if (root / path).is_file()
    ]
    if len(configurations) > 1:
        raise ValueError("Declare one native actionlint configuration")
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
        image("actionlint"),
        *[argument for path in configurations for argument in ("-config-file", path)],
        *[argument for pattern in ignores for argument in ("-ignore", pattern)],
        *paths,
        cwd=root,
    )
