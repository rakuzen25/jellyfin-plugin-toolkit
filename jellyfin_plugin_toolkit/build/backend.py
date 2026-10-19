"""Locked backend publication without host-owned assemblies in the payload."""

import os
import shutil
from pathlib import Path

from jellyfin_plugin_toolkit.common.binaries import HOST_LIBRARIES
from jellyfin_plugin_toolkit.common.process import executable, run
from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.dev.references import ensure


def reference_directory(ctx):
    return Path(
        os.environ.get("JELLYFIN_PLUGIN_REFERENCE_DIR", ctx.root / ".build/jellyfin")
    ).resolve()


def publish(ctx, output, web, *, configuration="Release", references=None):
    root = ctx.root
    ctx.generated()
    if (
        ctx.config.get("frontend", {}).get("enabled", False)
        and not (web / "config.html").is_file()
    ):
        raise ValueError("Frontend assets are missing; run ./plugin build frontend")
    references = references or ensure(ctx, reference_directory(ctx))
    if output.exists():
        shutil.rmtree(output)
    project = str(ctx.path(ctx.config["backend"]["entry"]))
    properties = [
        "-p:JellyfinReferenceDir=" + str(references),
        "-p:PluginWebAssets=" + str(web),
    ]
    run(
        executable("dotnet"),
        "restore",
        project,
        "--locked-mode",
        "--nologo",
        "-m:1",
        "-p:NuGetAuditMode=all",
        "-p:WarningsAsErrors=NU1900",
        *properties,
        cwd=root,
    )
    run(
        executable("dotnet"),
        "publish",
        project,
        "--no-restore",
        "-c",
        configuration,
        "-o",
        str(output),
        "--nologo",
        "-m:1",
        "-p:UseSharedCompilation=false",
        *properties,
        cwd=root,
    )
    for binary in output.glob("*.dll"):
        if binary.name.startswith(HOST_LIBRARIES):
            binary.unlink()


def maintained_csharp(ctx, pattern):
    roots = ctx.config.get("backend", {}).get("source_roots", ["src", "tests/backend"])
    return sorted(
        p
        for directory in roots
        for p in ctx.path(directory).rglob(pattern)
        if not {"bin", "obj"}.intersection(p.relative_to(ctx.root).parts)
    )


def prepare(ctx):
    ctx.generated()
    ensure(ctx, reference_directory(ctx))
    for project in maintained_csharp(ctx, "*.csproj"):
        run(
            executable("dotnet"),
            "restore",
            str(project),
            "--locked-mode",
            "-m:1",
            "-p:NuGetAudit=false",
            cwd=ctx.root,
        )


if __name__ == "__main__":
    prepare(Context.load())
