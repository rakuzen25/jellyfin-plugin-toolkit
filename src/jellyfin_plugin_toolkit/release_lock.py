"""Refresh only the lockfile on a bot-owned toolkit release PR using trusted code."""

import base64
import copy
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import quote

from jellyfin_plugin_toolkit.automation import commit_identity
from jellyfin_plugin_toolkit.binding_pr import api, eligible


def refresh(root: Path, repository: str, number: str | int, bot: str) -> None:
    pr = api(repository, f"pulls/{number}")
    if not eligible(pr, repository, bot):
        raise ValueError("Expected an open same-repository release bot PR")
    base = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    )
    if pr["base"]["sha"] != base:
        raise ValueError("Release base moved; rerun release preparation")
    project = api(repository, "contents/pyproject.toml?ref=" + pr["head"]["sha"])
    proposed_bytes = base64.b64decode(project["content"], validate=False)
    trusted = tomllib.loads((root / "pyproject.toml").read_text())
    proposed = tomllib.loads(proposed_bytes.decode())
    sanitized = copy.deepcopy(proposed)
    selected = proposed["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", selected):
        raise ValueError("Expected a stable package version")
    sanitized["project"]["version"] = trusted["project"]["version"]
    if sanitized != trusted:
        raise ValueError("Release PR changed Python settings beyond package version")
    # Resolve trusted settings, with only the release version supplied as data.
    with tempfile.TemporaryDirectory(prefix="jpt-release-lock-") as folder:
        destination = Path(folder)
        for name in ["uv.lock", ".python-version"]:
            (destination / name).write_bytes((root / name).read_bytes())
        text = (root / "pyproject.toml").read_text()
        text, count = re.subn(
            r'(?m)^version = "[^"\n]+"$', 'version = "' + selected + '"', text
        )
        if count != 1:
            raise ValueError("Ambiguous package version declaration")
        (destination / "pyproject.toml").write_text(text)
        subprocess.run(["uv", "lock", "--no-build"], cwd=destination, check=True)
        locked = (destination / "uv.lock").read_bytes()
    existing = api(repository, "contents/uv.lock?ref=" + pr["head"]["sha"])
    if base64.b64decode(existing["content"]) == locked:
        return
    identity = commit_identity()
    blob = api(
        repository,
        "git/blobs",
        "POST",
        {"encoding": "base64", "content": base64.b64encode(locked).decode()},
    )
    parent = api(repository, "git/commits/" + pr["head"]["sha"])
    tree = api(
        repository,
        "git/trees",
        "POST",
        {
            "base_tree": parent["tree"]["sha"],
            "tree": [
                {
                    "path": "uv.lock",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob["sha"],
                }
            ],
        },
    )
    commit = api(
        repository,
        "git/commits",
        "POST",
        {
            "message": "chore: lock release package version",
            "tree": tree["sha"],
            "parents": [pr["head"]["sha"]],
            "author": identity,
            "committer": identity,
        },
    )
    latest = api(repository, f"pulls/{number}")
    if not eligible(latest, repository, bot) or any(
        latest[side]["sha"] != pr[side]["sha"] for side in ["base", "head"]
    ):
        raise ValueError("Release PR changed before lockfile commit")
    api(
        repository,
        "git/refs/heads/" + quote(pr["head"]["ref"], safe="/"),
        "PATCH",
        {"sha": commit["sha"], "force": False},
    )
