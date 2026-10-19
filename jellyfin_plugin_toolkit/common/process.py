"""Explicit consumer context, executable selection, and checked subprocesses."""

import os
import subprocess
import sys


def executable(name):
    return os.environ.get("JELLYFIN_PLUGIN_" + name.upper(), name)


def run(*args, cwd, env=None):
    display = args
    if "--include" in args:
        boundary = args.index("--include") + 1
        display = (*args[:boundary], f"[{len(args) - boundary} maintained files]")
    print("$ " + " ".join(str(a) for a in display), flush=True)
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)


def python_checks(ctx):
    selected = os.environ.get("JELLYFIN_PLUGIN_PYTHON")
    local = ctx.root / ".venv/bin/python"
    selected = selected or (str(local) if local.exists() else sys.executable)
    pin = ctx.root / ".python-version"
    if pin.exists():
        actual = subprocess.check_output([selected, "--version"], text=True).strip()
        if actual != "Python " + pin.read_text().strip():
            raise ValueError(
                "Python check environment is outdated; run ./plugin bootstrap"
            )
    return selected
