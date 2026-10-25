"""Common commands with explicit, consumer-owned feature operations."""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context

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
    toolkit_environment,
)
from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT
from jellyfin_plugin_toolkit.operations import supports_default


def operation(
    ctx: Context, name: str, extra: Sequence[str] = (), *, kind: str = "commands"
) -> bool:
    spec = ctx.config.operations.get(name)
    if spec is None or getattr(spec, kind) is None:
        return False
    replacements = {
        "python": python_checks(ctx),
        "root": str(ctx.root),
        "toolkit": str(TOOLKIT_ROOT),
        "pnpm": executable("pnpm"),
        "dotnet": executable("dotnet"),
    }
    for command in getattr(spec, kind):
        argv: list[str] = []
        for argument in command:
            for key, value in replacements.items():
                argument = argument.replace("{" + key + "}", value)
            argv.append(argument)
        run(
            *argv,
            *extra,
            cwd=ctx.path(spec.cwd),
            env=toolkit_environment(ctx.root, TOOLKIT_ROOT),
        )
    return True


def dispatch_operation(
    ctx: Context, name: str, default: Callable[[], None], extra: Sequence[str] = ()
) -> None:
    if operation(ctx, name, extra):
        return
    spec = ctx.config.operations.get(name)
    if spec and spec.after is not None and not supports_default(name):
        raise ValueError("After hook requires a standard operation: " + name)
    default()
    operation(ctx, name, kind="after")


def verify_tools(ctx: Context) -> None:
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
            stdout=sys.stderr,
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


def bootstrap(ctx: Context) -> None:
    verify_tools(ctx)
    ctx.generated()
    if (ctx.root / "package.json").exists():
        run(executable("pnpm"), "install", "--frozen-lockfile", cwd=ctx.root)
    prepare(ctx)


def frontend_build(ctx: Context) -> None:
    ctx.generated()
    if ctx.config.frontend.enabled:
        run(
            "node",
            ctx.config.frontend.build or str(TOOLKIT_ROOT / "node/build.mjs"),
            cwd=ctx.root,
        )


def format_target(ctx: Context, target: str, fix: bool) -> None:
    from jellyfin_plugin_toolkit.checks.formatting import format_target as format_files

    dispatch_operation(
        ctx,
        ("format-" if fix else "format-check-") + target,
        lambda: format_files(ctx, target, fix),
    )


def check(ctx: Context, target: str) -> None:
    dispatch_operation(ctx, "check-" + target, lambda: check_default(ctx, target))


def check_default(ctx: Context, target: str) -> None:
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
        from jellyfin_plugin_toolkit.quality import javascript, typescript

        javascript(
            ctx.root,
            paths=[ctx.config.frontend.directory, ctx.config.browser.directory],
        )
        typescript(ctx.root / ctx.config.frontend.directory, ["tsconfig.json"])
    elif target == "tooling":
        from jellyfin_plugin_toolkit.checks.tooling import main

        format_target(ctx, "tooling", False)
        main(ctx)
    elif target == "automation":
        from jellyfin_plugin_toolkit.quality import plugin_automation

        format_target(ctx, "automation", False)
        plugin_automation(ctx)
    else:
        raise ValueError("Unknown check target: " + target)


def backend_tests(ctx: Context, extra: Sequence[str]) -> None:
    ctx.generated()
    frontend = ctx.config.frontend
    if frontend.enabled and any(
        not (ctx.root / "dist/web" / asset).is_file()
        for asset in frontend.required_assets
    ):
        frontend_build(ctx)
    for project in ctx.config.backend.tests:
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


def browser_tests(ctx: Context, extra: Sequence[str]) -> None:
    directory = ctx.config.browser.directory
    results = Path(tempfile.mkdtemp(prefix="results-run.", dir=ctx.root / directory))
    run(
        "node",
        str(TOOLKIT_ROOT / "node/check-browser-image.mjs"),
        "--root",
        str(ctx.root),
        cwd=ctx.root,
    )
    command = ["node", "/work/.build/jpt/node/run-browser.mjs", "/work"]
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


def test(ctx: Context, target: str, extra: Sequence[str]) -> None:
    dispatch_operation(
        ctx, "test-" + target, lambda: test_default(ctx, target, extra), extra
    )


def test_default(ctx: Context, target: str, extra: Sequence[str]) -> None:
    if target == "reproducibility":
        from jellyfin_plugin_toolkit.reproducibility import main

        main(ctx)
        return
    if target == "advisories":
        from jellyfin_plugin_toolkit.advisories import audit

        audit(ctx)
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
        run(
            "node",
            "--experimental-strip-types",
            "--test",
            *extra,
            cwd=ctx.root / ctx.config.frontend.directory,
        )
    elif target == "tooling":
        if (ctx.root / "tests/tooling").is_dir():
            run(
                python_checks(ctx),
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/tooling",
                *extra,
                cwd=ctx.root,
                env=toolkit_environment(ctx.root, TOOLKIT_ROOT),
            )
        else:
            print("No consumer tooling tests configured.")
    elif target == "browser":
        browser_tests(ctx, extra)
    else:
        raise ValueError("No test operation configured for " + target)
