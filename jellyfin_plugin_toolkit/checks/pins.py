"""Ensure local build tooling and reusable workflow pins identify one source revision."""

import json
import re

from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT


def check_pins(root, release=False):
    lock = json.loads((root / "toolkit.lock.json").read_text())
    metadata = (
        json.loads((TOOLKIT_ROOT / "toolkit-release.json").read_text())
        if (TOOLKIT_ROOT / "toolkit-release.json").exists()
        else {}
    )
    revision = metadata.get("revision", "")
    if metadata and metadata.get("version") != lock["version"].removeprefix("v"):
        raise ValueError("Toolkit archive and lock versions differ")
    if release and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Release toolkit must identify an immutable source revision")
    for workflow in [
        *(root / ".github/workflows").glob("*.yml"),
        *(root / ".github/workflows").glob("*.yaml"),
    ]:
        text = workflow.read_text()
        from jellyfin_plugin_toolkit.bindings import reference_tag, workflow_references

        for match in workflow_references(text, lock["repository"]):
            pin = match["revision"]
            tag = reference_tag(match)
            if tag is None or tag[0] != lock["version"]:
                raise ValueError(
                    "Toolkit workflow version comment and archive versions differ: "
                    + str(workflow)
                )
            if not re.fullmatch(r"[0-9a-f]{40}", pin) or pin != revision:
                raise ValueError(
                    "Toolkit archive/workflow pin mismatch: " + str(workflow)
                )
    renovate = root / "renovate.json"
    if renovate.is_file():
        config = json.loads(renovate.read_text())
        for preset in config.get("extends", []):
            prefix = "github>" + lock["repository"] + "#"
            if preset.startswith(prefix) and preset[len(prefix) :] != lock["version"]:
                raise ValueError("Toolkit preset and archive versions differ")
    if metadata:
        from jellyfin_plugin_toolkit.bindings import check_integrations, plan

        if plan(root):
            raise ValueError(
                "Toolkit bindings have drift; run ./plugin toolkit sync to review the repair"
            )
        from jellyfin_plugin_toolkit.context import Context

        check_integrations(root, Context.load(root).config)
    return metadata
