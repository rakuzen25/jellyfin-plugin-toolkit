"""Deterministic toolkit distributions with pinned source provenance."""

import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

EXCLUDE = {
    ".git",
    ".build",
    "dist",
    ".venv",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
}


def pack(root: Path, output: Path, *, release=False):
    pin = root / ".python-version"
    if pin.exists() and pin.read_text().strip() != sys.version.split()[0]:
        raise ValueError("Build toolkit archives with the pinned Python version")
    version = (root / "version.txt").read_text().strip()
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        revision = ""
    if release:
        status = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            text=True,
        )
        if status.strip() or len(revision) != 40:
            raise ValueError(
                "Release archives require a clean committed toolkit checkout"
            )
    files = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and not set(p.relative_to(root).parts) & EXCLUDE
        and not p.name.endswith(".pyc")
    }
    files["toolkit-release.json"] = (
        json.dumps(
            {"schema": 1, "version": version, "revision": revision}, sort_keys=True
        )
        + "\n"
    ).encode()
    files["toolkit-files.json"] = (
        json.dumps(
            {
                "schema": 1,
                "files": {
                    name: hashlib.sha256(contents).hexdigest()
                    for name, contents in sorted(files.items())
                },
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"jellyfin-plugin-toolkit-{version}.tar.gz"
    with (
        archive.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as package,
    ):
        for name, contents in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            info.mode = 0o755 if name in {"bootstrap/plugin", "toolkit"} else 0o644
            info.mtime = 0
            package.addfile(info, io.BytesIO(contents))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(checksum + "  " + archive.name + "\n")
    return archive, checksum
