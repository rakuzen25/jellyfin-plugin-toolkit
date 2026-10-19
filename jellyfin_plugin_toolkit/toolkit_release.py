"""Resolve toolkit drafts and attach the exact archive accepted by toolkit CI."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from jellyfin_plugin_toolkit.release.__main__ import (
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


def artifact_hashes(directory: Path, tag: str, sha: str) -> dict[str, str]:
    version = tag.removeprefix("v")
    name = f"jellyfin-plugin-toolkit-{version}.tar.gz"
    archive = directory / name
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if (directory / "SHA256SUMS").read_text() != f"{digest}  {name}\n":
        raise ValueError("Toolkit archive checksum or filename mismatch")
    with tarfile.open(archive, "r:gz") as package:
        members = package.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or any(
            not member.isfile()
            or Path(member.name).is_absolute()
            or ".." in Path(member.name).parts
            for member in members
        ):
            raise ValueError("Invalid toolkit archive inventory")

        def contents(filename):
            with package.extractfile(filename) as stream:
                return stream.read()

        metadata = json.loads(contents("toolkit-release.json"))
        if metadata != {"schema": 1, "version": version, "revision": sha}:
            raise ValueError("Toolkit archive version or revision mismatch")
        if contents("version.txt").decode().strip() != version:
            raise ValueError("Toolkit version.txt disagrees with the release tag")
        inventory = json.loads(contents("toolkit-files.json"))
        if inventory.get("schema") != 1 or set(inventory["files"]) != set(names) - {
            "toolkit-files.json"
        }:
            raise ValueError("Incomplete toolkit archive inventory")
        for filename, expected in inventory["files"].items():
            if hashlib.sha256(contents(filename)).hexdigest() != expected:
                raise ValueError("Toolkit inventory checksum mismatch: " + filename)
    return {
        name: digest,
        "SHA256SUMS": hashlib.sha256(
            (directory / "SHA256SUMS").read_bytes()
        ).hexdigest(),
    }


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
    print("Validated toolkit archive attached; publication remains manual.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["resolve", "upload"])
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            _, revision = resolve(args.repository, args.tag, args.sha)
            if args.output is None:
                parser.error("resolve requires --output")
            with args.output.open("a") as output:
                output.write(f"tag={args.tag}\nsha={revision}\n")
        else:
            if not args.sha:
                parser.error("upload requires --sha")
            upload(args.directory, args.repository, args.tag, args.sha)
    except (
        ValueError,
        KeyError,
        OSError,
        tarfile.TarError,
        subprocess.CalledProcessError,
    ) as error:
        parser.exit(1, f"Toolkit release verification failed: {error}\n")


if __name__ == "__main__":
    main()
