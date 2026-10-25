"""Cross-check native uv package provenance and immutable workflow bindings."""

import json
import re
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

from jellyfin_plugin_toolkit.bindings import (
    reference_tag,
    released_actions,
    workflow_references,
)
from jellyfin_plugin_toolkit.provenance import REPOSITORY, package_version, record


def check_pins(root: Path, release: bool = False) -> dict[str, Any]:
    root = Path(root)
    selected = record(root, release=release)
    if selected["version"] != package_version():
        metadata = distribution("jellyfin-plugin-toolkit").read_text("direct_url.json")
        editable = bool(
            metadata and json.loads(metadata).get("dir_info", {}).get("editable")
        )
        if release or not editable:
            raise ValueError(
                "Installed toolkit differs from uv.lock; run uv sync --locked"
            )
    pins: set[str] = set()
    components: set[str] = set()
    for workflow in (root / ".github/workflows").glob("*.y*ml"):
        for match in workflow_references(workflow.read_text(), REPOSITORY):
            tag = reference_tag(match)
            if (
                not tag
                or tag[0] != "v" + selected["version"]
                or not re.fullmatch(r"[a-f0-9]{40}", match["revision"])
            ):
                raise ValueError(
                    "Toolkit workflow and uv dependency versions differ: "
                    + str(workflow)
                )
            pins.add(match["revision"])
            components.add(match["component"])
    if len(pins) > 1:
        raise ValueError("Toolkit workflows disagree about the source revision")
    if release and pins:
        from jellyfin_plugin_toolkit.release.service import tag_commit

        if pins != {tag_commit(REPOSITORY, "v" + selected["version"])}:
            raise ValueError("Workflow revision differs from the released toolkit tag")
        released_actions(REPOSITORY, next(iter(pins)), components)
    preset = root / "renovate.json"
    if preset.exists():
        for name in json.loads(preset.read_text()).get("extends", []):
            if (
                any(
                    name.startswith("github>" + REPOSITORY + suffix)
                    for suffix in ("#", ":tooling#")
                )
                and name.rsplit("#", 1)[-1] != "v" + selected["version"]
            ):
                raise ValueError("Renovate preset differs from the toolkit release")
    return selected
