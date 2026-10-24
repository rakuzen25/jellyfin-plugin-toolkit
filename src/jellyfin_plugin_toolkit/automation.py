"""Resolve the existing GitHub App bot without inventing a commit identity."""

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field


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
    if not isinstance(record, dict) or type(record.get("schema_version")) is not int:
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


def check_renovate(root: Path, identity: dict[str, str]) -> None:
    from jellyfin_plugin_toolkit.bindings import safe_path

    message = "Merge the App setup PR, or run the App setup workflow to refresh the connected App identity"
    state = safe_path(root, STATE_PATH)
    if (
        not state.is_file()
        or record_identity(json.loads(state.read_text())) != identity
    ):
        raise ValueError(message)
    configuration = json.loads(safe_path(root, "renovate.json").read_text())
    if identity["email"] not in configuration.get("gitIgnoredAuthors", []):
        raise ValueError(message)
