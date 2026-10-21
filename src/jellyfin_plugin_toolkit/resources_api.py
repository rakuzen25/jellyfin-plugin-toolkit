"""Expose installed package data to Node, editor and MSBuild integrations."""

import fcntl
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path

RESOURCE_ROOT = Path(str(files("jellyfin_plugin_toolkit").joinpath("resources")))


def prepare(root):
    root = Path(root).resolve()
    build = root / ".build"
    target = build / "jpt"
    if build.is_symlink() or target.is_symlink():
        raise ValueError("Generated resources cannot redirect outside the checkout")
    build.mkdir(exist_ok=True)
    with (build / ".jpt-resources.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix="jpt-resources-", dir=build) as folder:
            staged = Path(folder) / "resources"
            shutil.copytree(
                RESOURCE_ROOT,
                staged,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            previous = Path(folder) / "previous"
            if target.exists():
                target.rename(previous)
            staged.rename(target)
    return target
