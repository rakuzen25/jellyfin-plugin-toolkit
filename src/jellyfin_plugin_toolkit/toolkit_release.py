"""Resolve toolkit drafts and attach the exact distributions accepted by toolkit CI."""

import email
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from jellyfin_plugin_toolkit.provenance import package_version, wheel_name
from jellyfin_plugin_toolkit.release.service import (
    release_for_tag,
    repository_name,
    tag_commit,
)


def resolve(repository: str, tag: str, sha: str = "") -> tuple[dict, str]:
    repository_name(repository)
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Expected a toolkit vMAJOR.MINOR.PATCH tag")
    if sha and not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an immutable toolkit source revision")
    release = release_for_tag(repository, tag)
    if not release or release.get("draft") is not True:
        raise ValueError("Only an existing unpublished toolkit draft is supported")
    revision = tag_commit(repository, tag)
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or (sha and revision != sha):
        raise ValueError(
            "Toolkit draft tag moved or does not match the validated revision"
        )
    return release, revision


def manifest(directory, sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an immutable source revision")
    selected = package_version()
    names = [wheel_name(selected), f"jellyfin_plugin_toolkit-{selected}.tar.gz"]
    hashes = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in names
    }
    (directory / "SHA256SUMS").write_text(
        "".join(value + "  " + name + "\n" for name, value in sorted(hashes.items()))
    )
    (directory / "build-provenance.json").write_text(
        json.dumps(
            {"schema": 2, "version": selected, "revision": sha, "files": hashes},
            sort_keys=True,
        )
        + "\n"
    )


def artifact_hashes(directory: Path, tag: str, sha: str) -> dict[str, str]:
    selected = tag.removeprefix("v")
    names = [wheel_name(selected), f"jellyfin_plugin_toolkit-{selected}.tar.gz"]
    hashes = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in names
    }
    expected = "".join(
        value + "  " + name + "\n" for name, value in sorted(hashes.items())
    )
    if (directory / "SHA256SUMS").read_text() != expected:
        raise ValueError("Distribution checksums differ")
    if json.loads((directory / "build-provenance.json").read_text()) != {
        "schema": 2,
        "version": selected,
        "revision": sha,
        "files": hashes,
    }:
        raise ValueError("Distribution source provenance differs")
    with zipfile.ZipFile(directory / names[0]) as wheel:
        metadata = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Invalid wheel metadata")
        info = email.message_from_bytes(wheel.read(metadata[0]))
        if info["Name"] != "jellyfin-plugin-toolkit" or info["Version"] != selected:
            raise ValueError("Wheel identity differs")
    with tarfile.open(directory / names[1]) as package:
        names_in_source = [
            m for m in package.getmembers() if m.name.endswith("/PKG-INFO")
        ]
        if len(names_in_source) != 1:
            raise ValueError("Invalid source distribution metadata")
        info = email.message_from_bytes(package.extractfile(names_in_source[0]).read())
        if info["Name"] != "jellyfin-plugin-toolkit" or info["Version"] != selected:
            raise ValueError("Source distribution identity differs")
    for name in ["SHA256SUMS", "build-provenance.json"]:
        hashes[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    return hashes


def upload(directory: Path, repository: str, tag: str, sha: str) -> None:
    release, revision = resolve(repository, tag, sha)
    hashes = artifact_hashes(directory, tag, revision)
    names = [asset["name"] for asset in release.get("assets", [])]
    if len(names) != len(set(names)) or set(names) - set(hashes):
        raise ValueError("Unexpected or duplicate toolkit release assets")
    missing = []
    with tempfile.TemporaryDirectory(prefix="toolkit-release-assets-") as temporary:
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
                raise ValueError("Conflicting existing toolkit release asset: " + name)
    # Recheck after downloads so a published or moved release cannot be retried.
    current, _ = resolve(repository, tag, revision)
    if current["id"] != release["id"]:
        raise ValueError("Toolkit draft changed during artifact verification")
    if missing:
        subprocess.run(
            ["gh", "release", "upload", tag, *missing, "--repo", repository], check=True
        )
    print("Validated toolkit distributions attached; publication remains manual.")
