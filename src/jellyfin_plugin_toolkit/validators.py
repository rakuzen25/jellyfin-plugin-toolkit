"""Toolkit-owned automation runtimes, shared by native and standalone checks."""

import json
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from jellyfin_plugin_toolkit.common.process import run
from jellyfin_plugin_toolkit.resources_api import RESOURCE_ROOT

Validator = Literal["actionlint", "renovate"]


def image(name: Validator) -> str:
    values = TypeAdapter(dict[str, str]).validate_json(
        (RESOURCE_ROOT / "configs/validators.json").read_bytes()
    )
    value = values[name]
    if "@sha256:" not in value:
        raise ValueError("Toolkit validator must be digest-pinned: " + name)
    return value


def renovate_test(root: Path, script: Path | None = None) -> None:
    from jellyfin_plugin_toolkit.context import relative

    arguments: list[str] = []
    if script is not None:
        selected = relative(root, str(script))
        if not selected.is_file():
            raise ValueError("Renovate test script does not exist: " + str(script))
        arguments = ["/work/" + selected.relative_to(root).as_posix()]
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "node",
        "-v",
        str(root) + ":/work:ro",
        "-w",
        "/work",
        image("renovate"),
        "/work/.build/jpt/node/renovate-test.mjs",
        *arguments,
        cwd=root,
    )


def reject_overrides(root: Path) -> None:
    """Native repository toolchain files may contain product pins only."""
    path = root / "toolchain.json"
    if path.is_file():
        values = json.loads(path.read_text())
        if {"actionlint", "renovate"}.intersection(values):
            raise ValueError(
                "Automation validator pins are toolkit-owned; remove local overrides"
            )
