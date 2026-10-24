"""Explicit consumer context, executable selection, and checked subprocesses."""

from pathlib import Path
from typing import TYPE_CHECKING, Mapping, TextIO

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context

import os
import subprocess
import sys


def executable(name: str) -> str:
    return os.environ.get("JELLYFIN_PLUGIN_" + name.upper(), name)


def run(
    *args: str | Path,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    stdout: int | TextIO | None = None,
) -> None:
    display = args
    if "--include" in args:
        boundary = args.index("--include") + 1
        display = (*args[:boundary], f"[{len(args) - boundary} maintained files]")
    print("$ " + " ".join(str(a) for a in display), flush=True, file=sys.stderr)
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, stdout=stdout, check=True)


def toolkit_environment(root: Path, toolkit_root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "JELLYFIN_PLUGIN_ROOT": str(root),
        "JELLYFIN_PLUGIN_TOOLKIT_ROOT": str(toolkit_root),
    }


def python_checks(ctx: Context) -> str:
    return sys.executable
