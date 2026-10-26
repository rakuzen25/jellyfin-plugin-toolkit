"""Explicit inputs whose changes require fresh network advisory observations."""

import fnmatch
import json
import os
import subprocess
from pathlib import Path

INPUTS = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "uv.toml",
    "package.json",
    "**/package.json",
    "pnpm-lock.yaml",
    "**/pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    ".npmrc",
    "**/.npmrc",
    ".node-version",
    "global.json",
    "**/*.csproj",
    "*.csproj",
    "**/packages.lock.json",
    "packages.lock.json",
    "Directory.Build.*",
    "**/Directory.Build.*",
    "Directory.Packages.*",
    "**/Directory.Packages.*",
    "NuGet.Config",
    "nuget.config",
    "**/NuGet.Config",
    "**/nuget.config",
    "plugin.toml",
    ".github/**",
    "vendor/**",
    "toolchain.json",
    "src/jellyfin_plugin_toolkit/advisories.py",
    "src/jellyfin_plugin_toolkit/audit_policy.py",
)


def required(changed: list[str], *, fresh: bool, unknown: bool) -> bool:
    return (
        fresh
        or unknown
        or any(
            fnmatch.fnmatchcase(path, pattern) for path in changed for pattern in INPUTS
        )
    )


def select(root: Path, *, fresh: bool = False) -> None:
    """Select standalone CI audits without installing development tooling."""
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    name = os.environ["GITHUB_EVENT_NAME"]
    base = (
        event.get("pull_request", {}).get("base", {}).get("sha", "")
        if name == "pull_request"
        else event.get("before", "")
    )
    changed: list[str] = []
    unknown = not base
    if base:
        result = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", base, "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        unknown = result.returncode != 0
        changed = result.stdout.splitlines() if not unknown else []
    chosen = required(
        changed,
        fresh=fresh or name in {"schedule", "workflow_dispatch"},
        unknown=unknown,
    )
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.write("required=" + str(chosen).lower() + "\n")
