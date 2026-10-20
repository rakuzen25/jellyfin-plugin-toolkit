"""Verified-archive bootstrap protocol 1; no package imports before cache validation."""

import fcntl
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath


def read_member(package: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    stream = package.extractfile(member)
    if stream is None:
        raise ValueError(f"Toolkit archive member is not a file: {member.name}")
    with stream:
        return stream.read()


def install(root, lock, payload):
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    cache = root / ".build/toolkit"
    if (root / ".build").is_symlink() or cache.is_symlink():
        raise ValueError("Toolkit cache cannot redirect outside the checkout")
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = cache / ".bootstrap.lock"
    if lock_path.is_symlink():
        raise ValueError("Toolkit bootstrap lock cannot be a symlink")
    with lock_path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        return _install_locked(root, lock, payload)


def validate_lock(lock):
    if (
        not isinstance(lock, dict)
        or lock.get("schema") != 1
        or not isinstance(lock.get("repository"), str)
        or not re.fullmatch(r"[\w.-]+/[\w.-]+", lock["repository"])
    ):
        raise ValueError("Invalid toolkit repository or lock schema")
    version, digest = lock.get("version", ""), lock.get("sha256", "")
    if (
        not isinstance(version, str)
        or not isinstance(digest, str)
        or not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[\w.]+)?", version)
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
    ):
        raise ValueError("Invalid toolkit version or SHA-256")


def archive_files(payload, lock):
    validate_lock(lock)
    if hashlib.sha256(payload).hexdigest() != lock["sha256"]:
        raise ValueError(
            "Toolkit archive SHA-256 mismatch; working installation preserved"
        )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as package:
        members = package.getmembers()
        names = set()
        total = 0
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or not member.name
                or member.name in names
            ):
                raise ValueError("Unsafe or duplicate toolkit archive path")
            if not member.isfile() and not member.isdir():
                raise ValueError(
                    "Toolkit archive contains a link or unsupported member"
                )
            if path.as_posix() != member.name.rstrip("/") or str(path) == ".":
                raise ValueError("Unsafe noncanonical toolkit archive path")
            names.add(member.name)
            total += member.size
            if total > 300_000_000:
                raise ValueError("Expanded toolkit archive exceeds size limit")
        required = {
            "toolkit-release.json",
            "toolkit-files.json",
            "toolkit-bindings.json",
            "bootstrap/plugin",
            "jellyfin_plugin_toolkit/__init__.py",
            "jellyfin_plugin_toolkit/cli.py",
            "jellyfin_plugin_toolkit/context.py",
            "node/build.mjs",
            "msbuild/Plugin.props",
            "configs/eslint.mjs",
            "configs/ruff.toml",
            "configs/prettier.mjs",
            "configs/tsconfig.json",
            "ui/lifecycle.ts",
            "LICENSE",
            "NOTICE",
        }
        entries = {member.name: member for member in members if member.isfile()}
        if not required <= entries.keys():
            raise ValueError(
                "Toolkit archive is incomplete; working installation preserved"
            )
        if (
            entries["toolkit-release.json"].size > 4096
            or entries["toolkit-files.json"].size > 2_000_000
        ):
            raise ValueError("Toolkit metadata exceeds size limit")
        metadata = json.loads(read_member(package, entries["toolkit-release.json"]))
        if (
            not isinstance(metadata, dict)
            or metadata.get("version") != lock["version"][1:]
            or metadata.get("schema") != 1
        ):
            raise ValueError("Toolkit archive version differs from lock")
        inventory = json.loads(read_member(package, entries["toolkit-files.json"]))
        expected = {
            name: hashlib.sha256(read_member(package, member)).hexdigest()
            for name, member in entries.items()
            if name != "toolkit-files.json"
        }
        if inventory != {"schema": 1, "files": expected}:
            raise ValueError("Toolkit source inventory is incomplete or altered")
        return {name: read_member(package, member) for name, member in entries.items()}


def load_archive(root, lock, download=False, supplied=None):
    validate_lock(lock)
    cache = root / ".build/toolkit"
    if (root / ".build").is_symlink() or cache.is_symlink():
        raise ValueError("Toolkit cache cannot redirect outside the checkout")
    archive = cache / (lock["sha256"] + ".tar.gz")
    embedded = root / "build-support/toolkit.tar.gz"
    if archive.is_symlink():
        raise ValueError("Toolkit archive cache cannot contain symlinks")
    if supplied or (
        embedded.is_file()
        and lock == json.loads((root / "toolkit.lock.json").read_text())
    ):
        source = Path(supplied) if supplied else embedded
    else:
        source = archive if archive.is_file() else embedded
    if supplied or source.is_file():
        with source.open("rb") as stream:
            payload = stream.read(100_000_001)
    else:
        if not download:
            raise ValueError("Locked toolkit is missing; run ./plugin bootstrap")
        version = lock["version"]
        url = f"https://github.com/{lock['repository']}/releases/download/{version}/jellyfin-plugin-toolkit-{version[1:]}.tar.gz"
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read(100_000_001)
    if len(payload) > 100_000_000:
        raise ValueError("Toolkit archive exceeds size limit")
    files = archive_files(payload, lock)
    return payload, files


def remove_cache_entry(path):
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _install_locked(root, lock, payload):
    files = archive_files(payload, lock)
    cache = root / ".build/toolkit"
    digest = lock["sha256"]
    archive = cache / (digest + ".tar.gz")
    if archive.is_symlink() or archive.with_suffix(".download").is_symlink():
        raise ValueError("Toolkit archive cache cannot contain symlinks")
    if not archive.is_file() or archive.read_bytes() != payload:
        temporary = archive.with_suffix(".download")
        temporary.write_bytes(payload)
        temporary.replace(archive)
    target = cache / digest
    backup = cache / (".replaced-" + digest)
    if backup.is_symlink():
        raise ValueError("Toolkit recovery directory cannot be a symlink")
    if backup.exists():
        if target.exists():
            remove_cache_entry(backup)
        else:
            backup.replace(target)
    valid = (
        target.is_dir()
        and not target.is_symlink()
        and not any(p.is_symlink() for p in target.rglob("*"))
        and {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
        == set(files)
        and all((target / name).read_bytes() == data for name, data in files.items())
    )
    if not valid:
        staged = Path(tempfile.mkdtemp(prefix="extract-", dir=cache))
        try:
            for name, data in files.items():
                destination = staged / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                destination.chmod(
                    0o755 if name in {"bootstrap/plugin", "toolkit"} else 0o644
                )
            if target.is_symlink():
                raise ValueError("Toolkit cache directory cannot be a symlink")
            if target.exists():
                target.replace(backup)
            try:
                staged.replace(target)
            except BaseException:
                if backup.exists():
                    backup.replace(target)
                raise
            if backup.exists():
                remove_cache_entry(backup)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
    selection = cache / (digest + ".lock.json")
    pending = selection.with_suffix(".next")
    if selection.is_symlink() or pending.is_symlink():
        raise ValueError("Toolkit selection record cannot be a symlink")
    pending.write_text(json.dumps(lock) + "\n")
    pending.replace(selection)
    current = cache / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("Toolkit current pointer must be a symlink")
    link = cache / ".current-next"
    link.unlink(missing_ok=True)
    link.symlink_to(target.name, target_is_directory=True)
    link.replace(current)
    sys.path.insert(0, str(target))
    os.environ["JELLYFIN_PLUGIN_ROOT"] = str(root)
    os.environ["JELLYFIN_PLUGIN_TOOLKIT_ROOT"] = str(target)
    return target


BOOTSTRAP_PROTOCOL = 1


def main(root, selected_lock, payload, argv):
    """Activate verified bytes and dispatch, returning the public command status."""
    install(root, selected_lock, payload)
    if argv[:1] == ["_install"]:
        if environment := os.environ.get("GITHUB_ENV"):
            with open(environment, "a") as stream:
                stream.writelines(
                    key + "=" + value + "\n"
                    for key, value in {
                        "PYTHONPATH": os.environ["JELLYFIN_PLUGIN_TOOLKIT_ROOT"],
                        **{
                            key: os.environ[key]
                            for key in (
                                "JELLYFIN_PLUGIN_ROOT",
                                "JELLYFIN_PLUGIN_TOOLKIT_ROOT",
                                "PYTHONDONTWRITEBYTECODE",
                            )
                        },
                    }.items()
                )
        return 0
    result = importlib.import_module("jellyfin_plugin_toolkit.cli").main(argv)
    return result if isinstance(result, int) else 0
