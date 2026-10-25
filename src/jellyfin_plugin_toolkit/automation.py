"""Resolve the existing GitHub App bot without inventing a commit identity."""

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from jellyfin_plugin_toolkit.common.json_shapes import is_json_array, is_json_object


def resolve_bot(slug: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Expected a GitHub App slug")
    login = slug + "[bot]"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "jellyfin-plugin-toolkit",
    }
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        "https://api.github.com/users/" + quote(login, safe=""), headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        user = json.load(response)
    if (
        user.get("login") != login
        or user.get("type") != "Bot"
        or type(user.get("id")) is not int
        or user["id"] <= 0
    ):
        raise ValueError("GitHub did not return the expected App bot identity")
    return {
        "name": login,
        "email": str(user["id"]) + "+" + login + "@users.noreply.github.com",
    }


def commit_identity() -> dict[str, str]:
    if not os.environ.get("GH_TOKEN"):
        raise ValueError("A GitHub App installation token is required for writes")
    return resolve_bot(os.environ.get("JPT_APP_SLUG", ""))


STATE_PATH = ".github/jpt-automation.json"


class AutomationRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    schema_version: Literal[1]
    app_slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    bot_id: int = Field(gt=0)


def record_identity(record: object) -> dict[str, str]:
    if not is_json_object(record) or type(record.get("schema_version")) is not int:
        raise ValueError("Invalid App ownership record")
    selected = AutomationRecord.model_validate(record)
    login = selected.app_slug + "[bot]"
    return {
        "name": login,
        "email": f"{selected.bot_id}+{login}@users.noreply.github.com",
    }


def identity_record(identity: dict[str, str]) -> dict[str, Any]:
    match = re.fullmatch(
        r"([1-9][0-9]*)\+([a-z0-9]+(?:-[a-z0-9]+)*)\[bot\]@users\.noreply\.github\.com",
        identity["email"],
    )
    if not match or identity["name"] != match[2] + "[bot]":
        raise ValueError("Invalid App bot identity")
    return {"schema_version": 1, "app_slug": match[2], "bot_id": int(match[1])}


def setup_guidance() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    location = ".github/workflows/app-setup.yml"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        location = (
            "https://github.com/" + repository + "/actions/workflows/app-setup.yml"
        )
    return (
        "Run Set up connected GitHub App ("
        + location
        + ") on the default branch and merge its configuration PR."
    )


def check_configuration(root: Path, identity: dict[str, str] | None = None) -> None:
    """Validate tracked data offline; live identity is required only for App writes."""
    from jellyfin_plugin_toolkit.bindings import safe_path

    state = safe_path(root, STATE_PATH)
    if not state.exists():
        message = "App setup is pending. " + setup_guidance()
        if identity is not None:
            raise ValueError(message)
        if safe_path(root, ".github/workflows/app-setup.yml").is_file():
            print("NOTICE: " + message)
        return
    try:
        recorded = record_identity(json.loads(state.read_text()))
    except (ValueError, OSError) as error:
        raise ValueError(
            "Invalid tracked App ownership record ("
            + STATE_PATH
            + "). Repair or restore the last valid record before running App setup."
        ) from error
    if identity is not None and recorded != identity:
        raise ValueError(
            "App setup required: Connected App identity differs from its tracked record. "
            + setup_guidance()
        )
    try:
        configuration = json.loads(safe_path(root, "renovate.json").read_text())
        if not is_json_object(configuration):
            raise ValueError("Expected a Renovate object")
        authors = configuration.get("gitIgnoredAuthors", [])
        if not is_json_array(authors) or any(
            not isinstance(item, str) for item in authors
        ):
            raise ValueError("Expected gitIgnoredAuthors to contain strings")
    except (ValueError, OSError) as error:
        raise ValueError(
            "Invalid tracked Renovate configuration; repair renovate.json before running App setup."
        ) from error
    if recorded["email"] not in authors:
        raise ValueError(
            "Renovate is missing the managed App identity. " + setup_guidance()
        )


def check_renovate(root: Path, identity: dict[str, str]) -> None:
    check_configuration(root, identity)
