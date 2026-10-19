"""Locked backend publication without host-owned assemblies in the payload."""

import os
import shutil
from pathlib import Path

from jellyfin_plugin_toolkit.common.process import ROOT, executable, run
from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.dev.references import ensure

HOST_LIBRARIES = (
    "MediaBrowser.",
    "Jellyfin.Api",
    "Jellyfin.Data",
    "Jellyfin.Database.",
    "Jellyfin.Extensions",
    "Microsoft.AspNetCore.",
    "Microsoft.Data.Sqlite",
    "SQLitePCLRaw.",
)


def reference_directory():
    return Path(
        os.environ.get("JELLYFIN_PLUGIN_REFERENCE_DIR", ROOT / ".build/jellyfin")
    ).resolve()


def publish(output, web, *, configuration="Release", root=ROOT, references=None):
    ctx = Context.load(root)
    ctx.generated()
    if (
        ctx.config.get("frontend", {}).get("enabled", False)
        and not (web / "config.html").is_file()
    ):
        raise ValueError("Frontend assets are missing; run ./plugin build frontend")
    references = references or ensure(reference_directory())
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


def maintained_csharp(pattern):
    ctx = Context.load()
    roots = ctx.config.get("backend", {}).get("source_roots", ["src", "tests/backend"])
    return sorted(
        p
        for directory in roots
        for p in ctx.path(directory).rglob(pattern)
        if not {"bin", "obj"}.intersection(p.relative_to(ROOT).parts)
    )


def prepare():
    Context.load().generated()
    ensure(reference_directory())
    for project in maintained_csharp("*.csproj"):
        run(
            executable("dotnet"),
            "restore",
            str(project),
            "--locked-mode",
            "-m:1",
            "-p:NuGetAudit=false",
        )


if __name__ == "__main__":
    prepare()
