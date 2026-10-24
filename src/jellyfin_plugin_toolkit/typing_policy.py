"""Shared typing policy materialized in native tool configuration."""

import re
import tomllib
from pathlib import Path

import tomli_w

from jellyfin_plugin_toolkit.resources_api import RESOURCE_ROOT


def materialize(text: str, policy: bytes | None = None) -> str:
    source = (
        policy
        if policy is not None
        else (RESOURCE_ROOT / "configs/mypy.toml").read_bytes()
    )
    values = tomllib.loads(source.decode())["tool"]["mypy"]
    if set(values) != {"strict"} or values["strict"] is not True:
        raise ValueError("Unsupported shared mypy policy")
    parsed = tomllib.loads(text)
    section = parsed.get("tool", {}).get("mypy")
    if section is None:
        raise ValueError(
            "Declare native [tool.mypy] paths before synchronizing typing policy"
        )
    if all(section.get(key) == value for key, value in values.items()):
        return text
    match = re.search(r"(?m)^\[tool\.mypy\][ \t]*(?:#[^\n]*)?$", text)
    if match is None:
        raise ValueError("Shared policy requires an unquoted [tool.mypy] table")
    following = re.search(r"(?m)^\[", text[match.end() :])
    end = match.end() + following.start() if following else len(text)
    body = text[match.end() : end]
    for key, value in values.items():
        assignment = re.compile(r"(?m)^" + re.escape(key) + r"[ \t]*=[^\n]*$")
        rendered = tomli_w.dumps({key: value}).strip()
        if assignment.search(body):
            body = assignment.sub(rendered, body)
        else:
            body = "\n" + rendered + body
    result = text[: match.end()] + body + text[end:]
    tomllib.loads(result)
    return result


def check_policy(root: Path) -> None:
    path = root / "pyproject.toml"
    text = path.read_text()
    if materialize(text) != text:
        raise ValueError(
            "Shared mypy policy is stale; run uv run jpt toolkit sync --apply"
        )
    settings = tomllib.loads(text).get("tool", {})
    based = settings.get("basedpyright", {})
    preset = (RESOURCE_ROOT / "configs/basedpyright.toml").read_bytes()
    shared = tomllib.loads(preset.decode())["tool"]["basedpyright"]
    if not str(based.get("extends", "")).endswith("configs/basedpyright.toml"):
        raise ValueError(
            "Native basedpyright configuration must extend the shared preset"
        )
    if any(key in based and based[key] != value for key, value in shared.items()):
        raise ValueError(
            "Native basedpyright configuration weakens shared typing policy"
        )
