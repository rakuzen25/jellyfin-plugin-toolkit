"""Prepare hash-locked binary wheels for the pinned toolkit release platform."""

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def identity(root):
    return {
        "requirements": hashlib.sha256(
            (root / "requirements-runtime.txt").read_bytes()
        ).hexdigest(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "machine": platform.machine(),
    }


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Runtime dependencies must not contain symlinks")
        if path.is_file():
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            files[path.relative_to(directory).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return files


def verify(root):
    manifest = json.loads((root / ".build/runtime.json").read_text())
    if (
        manifest != {**identity(root), "files": inventory(root / ".build/runtime")}
        or not manifest["files"]
    ):
        raise ValueError(
            "Prepared runtime differs from its lock or inventory; run python3 scripts/runtime.py"
        )


def main():
    root = Path(__file__).resolve().parents[1]
    if sys.version.split()[0] != (root / ".python-version").read_text().strip() or (
        sys.platform,
        platform.machine(),
    ) != ("linux", "x86_64"):
        raise ValueError(
            "Prepare toolkit dependencies with the pinned Python on Linux x64"
        )
    build = root / ".build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runtime-", dir=build) as temporary:
        target = Path(temporary) / "packages"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--only-binary=:all:",
                "--no-compile",
                "--uploaded-prior-to=P1D",
                "--target",
                str(target),
                "-r",
                str(root / "requirements-runtime.txt"),
            ],
            check=True,
        )
        manifest = {**identity(root), "files": inventory(target)}
        shutil.rmtree(build / "runtime", ignore_errors=True)
        target.rename(build / "runtime")
        (build / "runtime.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    verify(root)


if __name__ == "__main__":
    main()
