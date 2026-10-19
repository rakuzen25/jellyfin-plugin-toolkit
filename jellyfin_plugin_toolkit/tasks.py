"""Common commands with explicit, consumer-owned feature operations."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jellyfin_plugin_toolkit.build.backend import (
    maintained_csharp,
    prepare,
    reference_directory,
)
from jellyfin_plugin_toolkit.common.process import (
    IMAGES,
    ROOT,
    executable,
    python_checks,
    run,
)
from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT, Context
from jellyfin_plugin_toolkit.dev.references import verify

FRONTEND_FORMAT = [
    "frontend/**/*.{ts,mjs,json,css,html}",
    "tests/browser/**/*.{cjs,mjs,json}",
]
DOCS_FORMAT = ["*.md", "docs/**/*.md"]


def operation(name, extra=()):
    ctx = Context.load()
    spec = ctx.config.get("operations", {}).get(name)
    if spec is None:
        return False
    replacements = {
        "python": python_checks(),
        "root": str(ROOT),
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
                "PYTHONPATH": str(TOOLKIT_ROOT) + os.pathsep + str(ROOT),
            },
        )
    return True


def verify_tools():
    Context.load()
    commands = ["docker", executable("dotnet"), "node"]
    if (ROOT / "package.json").exists():
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
        path = ROOT / pin
        if not path.exists():
            continue
        expected = (
            json.loads(path.read_text())["sdk"]["version"]
            if pin == "global.json"
            else path.read_text().strip()
        )
        if actual != expected:
            raise ValueError(f"{pin} requires {expected}; found {actual}")
    if (ROOT / ".node-version").exists():
        expected = (ROOT / ".node-version").read_text().strip()
        actual = (
            subprocess.check_output(["node", "--version"], text=True)
            .strip()
            .removeprefix("v")
        )
        if expected != actual:
            raise ValueError(f"Node {expected} required; found {actual}")
    if (ROOT / "package.json").exists():
        for tool, pin in [
            ("node", ROOT / ".node-version"),
            ("pnpm", ROOT / "package.json"),
        ]:
            if not pin.exists():
                continue
            expected = (
                json.loads(pin.read_text())["engines"]["pnpm"]
                if tool == "pnpm"
                else pin.read_text().strip()
            )
            actual = (
                subprocess.check_output([executable(tool), "--version"], text=True)
                .strip()
                .removeprefix("v")
            )
            if expected != actual:
                raise ValueError(f"{tool} requires {expected}; found {actual}")


def bootstrap():
    verify_tools()
    Context.load().generated()
    if (ROOT / "package.json").exists():
        run(executable("pnpm"), "install", "--frozen-lockfile")
    requirements = ROOT / "requirements-check.txt"
    if requirements.exists():
        local = ROOT / ".venv/bin/python"
        if not local.exists():
            run(sys.executable, "-m", "venv", str(ROOT / ".venv"))
        run(
            str(local),
            "-m",
            "jellyfin_plugin_toolkit.checks.check_pip",
            env={
                **os.environ,
                "PYTHONPATH": str(TOOLKIT_ROOT) + os.pathsep + str(ROOT),
            },
        )
        run(
            str(local),
            "-m",
            "pip",
            "install",
            "--uploaded-prior-to=P1D",
            "-r",
            str(requirements),
        )
    prepare()


def frontend_build():
    ctx = Context.load()
    ctx.generated()
    if ctx.config.get("frontend", {}).get("enabled", False):
        run("node", ctx.config["frontend"].get("build", "frontend/build.mjs"))


def dotnet_format(fix=False):
    if not verify(reference_directory()):
        raise ValueError("Verified references are missing; run ./plugin bootstrap")
    files = [str(p) for p in maintained_csharp("*.cs")]
    for project in maintained_csharp("*.csproj"):
        if not (project.parent / "obj/project.assets.json").exists():
            raise ValueError("Backend dependencies are missing; run ./plugin bootstrap")
        run(
            executable("dotnet"),
            "format",
            str(project),
            "--no-restore",
            *([] if fix else ["--verify-no-changes"]),
            "--include",
            *files,
        )


def format_target(target, fix):
    if operation(("format-" if fix else "format-check-") + target):
        return
    if target == "backend":
        dotnet_format(fix)
    else:
        patterns = (
            DOCS_FORMAT
            if target == "docs"
            else FRONTEND_FORMAT
            if target == "frontend"
            else [
                "scripts/**/*.{mjs,json}",
                "*.{json,mjs,yaml}",
                ".github/**/*.{yml,yaml}",
            ]
        )
        run(
            executable("pnpm"),
            "exec",
            "prettier",
            "--write" if fix else "--check",
            "--no-error-on-unmatched-pattern",
            *patterns,
        )
        if target == "tooling":
            run(
                executable("pnpm"),
                "exec",
                "taplo",
                "format",
                *([] if fix else ["--check"]),
                "plugin.toml",
            )


def check(target):
    if operation("check-" + target):
        return
    if target == "backend":
        from jellyfin_plugin_toolkit.checks.architecture import check_projects

        check_projects(ROOT)
        dotnet_format(False)
    elif target == "docs":
        format_target("docs", False)
        from jellyfin_plugin_toolkit.checks.docs import main

        main()
    elif target == "frontend":
        run(
            executable("pnpm"),
            "--dir",
            Context.load().config["frontend"].get("directory", "frontend"),
            "exec",
            "tsc",
            "--noEmit",
        )
    elif target == "tooling":
        from jellyfin_plugin_toolkit.checks.pins import check_pins

        check_pins(ROOT)
        from jellyfin_plugin_toolkit.common.source_inventory import source_files

        for path in source_files(ROOT):
            if path.suffix == ".py":
                compile((ROOT / path).read_bytes(), str(path), "exec")
        run(executable("pnpm"), "exec", "taplo", "format", "--check", "plugin.toml")
        from jellyfin_plugin_toolkit.checks.workflows import check_workflows

        check_workflows(ROOT)
    else:
        raise ValueError("Unknown check target: " + target)


def backend_tests(extra):
    ctx = Context.load()
    ctx.generated()
    for project in ctx.config["backend"].get("tests", []):
        run(
            executable("dotnet"),
            "restore",
            project,
            "--locked-mode",
            "-m:1",
            "-p:NuGetAudit=false",
            "-p:JellyfinReferenceDir=" + str(reference_directory()),
        )
        run(
            executable("dotnet"),
            "test",
            "--project",
            project,
            "--no-restore",
            "--minimum-expected-tests",
            "1",
            "-p:JellyfinReferenceDir=" + str(reference_directory()),
            *extra,
            env={**os.environ, "TESTINGPLATFORM_TELEMETRY_OPTOUT": "1"},
        )


def browser_tests(extra):
    ctx = Context.load()
    directory = ctx.config.get("browser", {}).get("directory", "tests/browser")
    results = Path(tempfile.mkdtemp(prefix="results-run.", dir=ROOT / directory))
    run("node", str(TOOLKIT_ROOT / "node/check-browser-image.mjs"), "--root", str(ROOT))
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
        str(ROOT) + ":/work",
        "-w",
        "/work/" + directory,
        IMAGES["playwright"],
        *command,
        *extra,
    )


def test(target, extra):
    if operation("test-" + target, extra):
        return
    if target == "reproducibility":
        from jellyfin_plugin_toolkit.reproducibility import main

        main()
        return
    if target == "advisories":
        if (ROOT / "pnpm-lock.yaml").exists():
            run(executable("pnpm"), "audit")
        for project in Context.load().config["backend"].get("tests", []):
            run(
                executable("dotnet"),
                "restore",
                project,
                "--locked-mode",
                "-m:1",
                "--force-evaluate",
                "-p:NuGetAuditMode=all",
                "-p:WarningsAsErrors=NU1900%3BNU1901%3BNU1902%3BNU1903%3BNU1904%3BNU1905",
            )
        return
    if target == "web-installation":
        return  # Optional plugin-owned standalone web installer.
    if target == "artifact-inventory":
        from jellyfin_plugin_toolkit.acceptance import inventory

        inventory()
        return
    if target == "installation":
        from jellyfin_plugin_toolkit.installation import main

        main()
        return
    if target == "backend":
        backend_tests(extra)
    elif target == "frontend":
        run(
            "node",
            "--experimental-strip-types",
            "--test",
            *extra,
            cwd=ROOT / Context.load().config["frontend"].get("directory", "frontend"),
        )
    elif target == "browser":
        browser_tests(extra)
    else:
        raise ValueError("No test operation configured for " + target)
