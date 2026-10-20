"""Common commands with explicit, consumer-owned feature operations."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jellyfin_plugin_toolkit.build.backend import (
    prepare,
    reference_directory,
)
from jellyfin_plugin_toolkit.common.process import (
    executable,
    python_checks,
    run,
)
from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT


def operation(ctx, name, extra=()):
    spec = ctx.config.get("operations", {}).get(name)
    if spec is None:
        return False
    replacements = {
        "python": python_checks(ctx),
        "root": str(ctx.root),
        "toolkit": str(TOOLKIT_ROOT),
        "pnpm": executable("pnpm"),
        "dotnet": executable("dotnet"),
    }
    for command in spec.get("commands", []):
        argv = []
        for argument in command:
            for key, value in replacements.items():
                argument = argument.replace("{" + key + "}", value)
            argv.append(argument)
        run(
            *argv,
            *extra,
            cwd=ctx.path(spec.get("cwd", ".")),
            env={
                **os.environ,
                "PYTHONPATH": str(TOOLKIT_ROOT) + os.pathsep + str(ctx.root),
            },
        )
    return True


def verify_tools(ctx):
    commands = ["docker", executable("dotnet"), "node"]
    if (ctx.root / "package.json").exists():
        commands += [executable("pnpm")]
    for command in commands:
        if not shutil.which(command):
            raise ValueError("Missing " + command + "; install the pinned toolchain")
    for pin, actual in [
        (".python-version", sys.version.split()[0]),
        (
            "global.json",
            subprocess.check_output(
                [executable("dotnet"), "--version"], text=True
            ).strip(),
        ),
    ]:
        path = ctx.root / pin
        if not path.exists():
            continue
        expected = (
            json.loads(path.read_text())["sdk"]["version"]
            if pin == "global.json"
            else path.read_text().strip()
        )
        if actual != expected:
            raise ValueError(f"{pin} requires {expected}; found {actual}")
    if (ctx.root / "package.json").exists():
        run(
            "node",
            str(TOOLKIT_ROOT / "node/check-toolchain.mjs"),
            "--root",
            str(ctx.root),
            cwd=ctx.root,
        )
    elif (ctx.root / ".node-version").exists():
        expected = (ctx.root / ".node-version").read_text().strip()
        actual = (
            subprocess.check_output(["node", "--version"], text=True)
            .strip()
            .removeprefix("v")
        )
        if expected != actual:
            raise ValueError(f"Node {expected} required; found {actual}")


def bootstrap(ctx):
    verify_tools(ctx)
    ctx.generated()
    if (ctx.root / "package.json").exists():
        run(executable("pnpm"), "install", "--frozen-lockfile", cwd=ctx.root)
    requirements = ctx.root / "requirements-check.txt"
    if requirements.exists():
        local = ctx.root / ".venv/bin/python"
        if not local.exists():
            run(sys.executable, "-m", "venv", str(ctx.root / ".venv"), cwd=ctx.root)
        run(
            str(local),
            "-m",
            "jellyfin_plugin_toolkit.checks.check_pip",
            env={
                **os.environ,
                "PYTHONPATH": str(TOOLKIT_ROOT) + os.pathsep + str(ctx.root),
            },
            cwd=ctx.root,
        )
        run(
            str(local),
            "-m",
            "pip",
            "install",
            "--uploaded-prior-to=P1D",
            "-r",
            str(requirements),
            cwd=ctx.root,
        )
    prepare(ctx)


def frontend_build(ctx):
    ctx.generated()
    if ctx.config.get("frontend", {}).get("enabled", False):
        run(
            "node",
            ctx.config["frontend"].get("build") or str(TOOLKIT_ROOT / "node/build.mjs"),
            cwd=ctx.root,
        )


def format_target(ctx, target, fix):
    if not operation(ctx, ("format-" if fix else "format-check-") + target):
        from jellyfin_plugin_toolkit.checks.formatting import (
            format_target as format_files,
        )

        format_files(ctx, target, fix)


def check(ctx, target):
    if operation(ctx, "check-" + target):
        return
    if target == "backend":
        from jellyfin_plugin_toolkit.checks.architecture import check_projects

        check_projects(ctx.root)
        from jellyfin_plugin_toolkit.checks.formatting import dotnet_format

        dotnet_format(ctx, False)
    elif target == "docs":
        format_target(ctx, "docs", False)
        from jellyfin_plugin_toolkit.checks.docs import main

        main(ctx)
    elif target == "frontend":
        format_target(ctx, "frontend", False)
        run(
            executable("pnpm"),
            "exec",
            "eslint",
            ".",
            "--max-warnings",
            "0",
            cwd=ctx.root,
        )
        run(
            executable("pnpm"),
            "--dir",
            ctx.config["frontend"].get("directory", "frontend"),
            "exec",
            "tsc",
            "--noEmit",
            cwd=ctx.root,
        )
    elif target == "tooling":
        from jellyfin_plugin_toolkit.checks.tooling import main

        format_target(ctx, "tooling", False)
        main(ctx)
    else:
        raise ValueError("Unknown check target: " + target)


def backend_tests(ctx, extra):
    ctx.generated()
    frontend = ctx.config.get("frontend", {})
    if frontend.get("enabled") and any(
        not (ctx.root / "dist/web" / asset).is_file()
        for asset in frontend.get("required_assets", ["config.html"])
    ):
        frontend_build(ctx)
    for project in ctx.config["backend"].get("tests", []):
        run(
            executable("dotnet"),
            "restore",
            project,
            "--locked-mode",
            "-m:1",
            "-p:NuGetAudit=false",
            "-p:JellyfinReferenceDir=" + str(reference_directory(ctx)),
            cwd=ctx.root,
        )
        run(
            executable("dotnet"),
            "test",
            "--project",
            project,
            "--no-restore",
            "--minimum-expected-tests",
            "1",
            "-p:JellyfinReferenceDir=" + str(reference_directory(ctx)),
            *extra,
            env={**os.environ, "TESTINGPLATFORM_TELEMETRY_OPTOUT": "1"},
            cwd=ctx.root,
        )


def browser_tests(ctx, extra):
    directory = ctx.config.get("browser", {}).get("directory", "tests/browser")
    results = Path(tempfile.mkdtemp(prefix="results-run.", dir=ctx.root / directory))
    run(
        "node",
        str(TOOLKIT_ROOT / "node/check-browser-image.mjs"),
        "--root",
        str(ctx.root),
        cwd=ctx.root,
    )
    command = ["node", "/work/.build/toolkit/current/node/run-browser.mjs", "/work"]
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-e",
        "CI_BROWSER_GROUP",
        "-e",
        "CI_BROWSER_OUTPUT_DIR=" + results.name,
        "-v",
        str(ctx.root) + ":/work",
        "-w",
        "/work/" + directory,
        ctx.images["playwright"],
        *command,
        *extra,
        cwd=ctx.root,
    )


def test(ctx, target, extra):
    if operation(ctx, "test-" + target, extra):
        return
    if target == "reproducibility":
        from jellyfin_plugin_toolkit.reproducibility import main

        main(ctx)
        return
    if target == "advisories":
        if (ctx.root / "pnpm-lock.yaml").exists():
            run(executable("pnpm"), "audit", cwd=ctx.root)
        for project in ctx.config["backend"].get("tests", []):
            run(
                executable("dotnet"),
                "restore",
                project,
                "--locked-mode",
                "-m:1",
                "--force-evaluate",
                "-p:NuGetAuditMode=all",
                "-p:WarningsAsErrors=NU1900%3BNU1901%3BNU1902%3BNU1903%3BNU1904%3BNU1905",
                cwd=ctx.root,
            )
        return
    if target == "web-installation":
        return  # Optional plugin-owned standalone web installer.
    if target == "artifact-inventory":
        from jellyfin_plugin_toolkit.acceptance import inventory

        inventory(ctx)
        return
    if target == "installation":
        from jellyfin_plugin_toolkit.installation import main

        main(ctx)
        return
    if target == "backend":
        backend_tests(ctx, extra)
    elif target == "frontend":
        format_target(ctx, "frontend", False)
        run(
            executable("pnpm"),
            "exec",
            "eslint",
            ".",
            "--max-warnings",
            "0",
            cwd=ctx.root,
        )
        run(
            "node",
            "--experimental-strip-types",
            "--test",
            *extra,
            cwd=ctx.root / ctx.config["frontend"].get("directory", "frontend"),
        )
    elif target == "browser":
        browser_tests(ctx, extra)
    else:
        raise ValueError("No test operation configured for " + target)
