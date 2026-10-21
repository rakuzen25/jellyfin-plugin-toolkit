"""Validate release identity and attach verified artifacts to an existing draft.

Never creates/publishes releases, rewrites tags, overwrites assets, or changes catalogs.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from jellyfin_plugin_toolkit.common.release_metadata import load_release
from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.release.configuration import (
    check as check_config,
)
from jellyfin_plugin_toolkit.release.configuration import (
    internal_references,
)


def check_identity(root: Path, tag: str = "") -> str:
    release = load_release(root)
    expected = "v" + release.version
    if (root / "version.txt").read_text().strip() != release.version:
        raise ValueError("Release version metadata disagrees")
    if tag and tag != expected:
        raise ValueError("Release tag does not match plugin metadata")
    ctx = Context.load(root)
    _, locks = internal_references(ctx)
    for path, rows in locks.items():
        for framework, project, dependency, value in rows:
            expected_range = f"[{release.version}, )"
            if value != expected_range:
                raise ValueError(
                    f"Project-version metadata is stale in {path}: "
                    f"{framework}/{project} dependency {dependency} has {value!r}; "
                    f"expected {expected_range!r}"
                )
    return expected


def repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Expected a GitHub owner/repository name")
    return value


def prepare(root: Path, repository: str, tag: str, environment: Path) -> None:
    repository = repository_name(repository)
    check_config(root)
    tag = check_identity(root, tag)
    with environment.open("a") as output:
        output.write(
            "JELLYFIN_PLUGIN_REPOSITORY_URL=https://github.com/" + repository + "\n"
        )
        output.write(
            "JELLYFIN_PLUGIN_DOWNLOAD_BASE_URL=https://github.com/"
            + repository
            + "/releases/download/"
            + tag
            + "\n"
        )


def github_json(repository: str, endpoint: str):
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise ValueError("GH_TOKEN is required to inspect the draft release")
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository + endpoint,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "Jellyfin-plugin-toolkit-release",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError(
            "GitHub release inspection failed (HTTP " + str(error.code) + ")"
        ) from None


def artifact_hashes(
    root: Path, directory: Path, repository: str, tag: str
) -> dict[str, str]:
    release = load_release(root)
    ctx = Context.load(root)
    prefix = ctx.prefix
    expected = {
        f"{prefix}-{release.version}.zip",
        f"{prefix}-source-{release.version}.zip",
        "manifest.json",
    }
    if ctx.config.distribution.web:
        expected.add(f"{prefix}-web-{release.version}.zip")
    hashes = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if (
            name not in expected
            or name in hashes
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            raise ValueError("Unexpected or duplicate release checksum entry")
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Release artifact checksum mismatch: " + name)
        hashes[name] = digest
    if set(hashes) != expected:
        raise ValueError("Release checksums do not cover all required artifacts")
    catalog = json.loads((directory / "manifest.json").read_text())
    if (
        len(catalog) != 1
        or catalog[0]["guid"] != release.guid
        or len(catalog[0]["versions"]) != 1
    ):
        raise ValueError("Unexpected plugin catalog identity")
    version = catalog[0]["versions"][0]
    binary = f"{prefix}-{release.version}.zip"
    if (
        version["version"] != release.jellyfin_version
        or version["targetAbi"] != release.abi
        or version["sourceUrl"]
        != f"https://github.com/{repository}/releases/download/{tag}/{binary}"
        or version["checksum"].lower()
        != hashlib.md5((directory / binary).read_bytes()).hexdigest()
    ):
        raise ValueError("Release catalog version, ABI, URL or checksum mismatch")
    hashes["SHA256SUMS"] = hashlib.sha256(
        (directory / "SHA256SUMS").read_bytes()
    ).hexdigest()
    return hashes


def release_for_tag(repository: str, tag: str) -> dict | None:
    matches: list[dict] = []
    page = 1
    while True:
        releases = github_json(repository, f"/releases?per_page=100&page={page}")
        matches.extend(
            candidate for candidate in releases if candidate.get("tag_name") == tag
        )
        if len(releases) < 100:
            break
        page += 1
    if len(matches) > 1:
        raise ValueError(
            "Expected exactly one release for this tag; resolve duplicate releases"
        )
    return matches[0] if matches else None


def tag_commit(repository: str, tag: str) -> str:
    reference = github_json(repository, "/git/ref/tags/" + tag)["object"]
    while reference["type"] == "tag":
        reference = github_json(repository, "/git/tags/" + reference["sha"])["object"]
    if reference["type"] != "commit":
        raise ValueError("Release tag does not identify a commit")
    return reference["sha"]


def resume_draft(root: Path, repository: str, output: Path) -> None:
    repository = repository_name(repository)
    tag = check_identity(root)
    draft = release_for_tag(repository, tag)
    if not draft or not draft.get("draft"):
        with output.open("a") as stream:
            stream.write("release_created=false\n")
        return
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    if tag_commit(repository, tag) != commit:
        raise ValueError(
            "Existing draft tag does not match main; resolve it explicitly before resuming"
        )
    with output.open("a") as stream:
        stream.write(f"release_created=true\ntag={tag}\nsha={commit}\n")


def upload(root: Path, directory: Path, repository: str, tag: str) -> None:
    repository = repository_name(repository)
    if not tag:
        raise ValueError("A release tag is required for artifact upload")
    check_identity(root, tag)
    hashes = artifact_hashes(root, directory, repository, tag)
    # The authenticated receipt is verified by ci.py before this command. It is
    # deliberately outside its own artifact hash set and SHA256SUMS.
    receipt = json.loads((directory / "validation.json").read_text())
    if receipt.get("repository") != repository or receipt.get("artifacts") != hashes:
        raise ValueError("Public validation receipt does not match release artifacts")
    hashes["validation.json"] = hashlib.sha256(
        (directory / "validation.json").read_bytes()
    ).hexdigest()
    # The release-by-tag endpoint does not reliably resolve draft releases.
    release = release_for_tag(repository, tag)
    if not release:
        raise ValueError(
            "Expected exactly one release for this tag; resolve missing releases before upload"
        )
    if release.get("draft") is not True:
        raise ValueError(
            "Only the matching existing draft release may receive artifacts"
        )
    tagged_commit = tag_commit(repository, tag)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    if tagged_commit != commit:
        raise ValueError("Release tag moved or does not identify the tested checkout")
    names = [asset["name"] for asset in release.get("assets", [])]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate existing release assets")
    missing = []
    with tempfile.TemporaryDirectory(
        prefix="jellyfin-plugin-existing-assets-"
    ) as temporary:
        for name, digest in hashes.items():
            if name not in names:
                missing.append(str(directory / name))
                continue
            subprocess.run(
                [
                    "gh",
                    "release",
                    "download",
                    tag,
                    "--repo",
                    repository,
                    "--pattern",
                    name,
                    "--dir",
                    temporary,
                ],
                check=True,
            )
            if (
                hashlib.sha256((Path(temporary) / name).read_bytes()).hexdigest()
                != digest
            ):
                raise ValueError("Conflicting existing release asset: " + name)
    if missing:
        subprocess.run(
            ["gh", "release", "upload", tag, *missing, "--repo", repository], check=True
        )
    print(
        "Verified artifacts attached; release remains a draft. Publish it after reviewing the successful workflow."
    )
