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


def toolkit_environment(root, toolkit_root):
    return {
        **os.environ,
        "JELLYFIN_PLUGIN_ROOT": str(root),
        "JELLYFIN_PLUGIN_TOOLKIT_ROOT": str(toolkit_root),
    }


def python_checks(ctx):
    return sys.executable
