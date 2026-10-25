"""Consumer formatting without task orchestration dependencies."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context

from jellyfin_plugin_toolkit.build.backend import maintained_csharp, reference_directory
from jellyfin_plugin_toolkit.common.process import executable, run
from jellyfin_plugin_toolkit.dev.references import verify

FRONTEND_FORMAT = [
    "frontend/**/*.{ts,mjs,json,css,html}",
    "tests/browser/**/*.{cjs,mjs,json}",
]
DOCS_FORMAT = ["*.md", "docs/**/*.md"]
AUTOMATION_FORMAT = ["renovate.json", ".github/**/*.{json,yml,yaml}"]


def dotnet_format(ctx: Context, fix: bool = False) -> None:
    if not verify(ctx, reference_directory(ctx)):
        raise ValueError("Verified references are missing; run uv run jpt bootstrap")
    files = [str(p) for p in maintained_csharp(ctx, "*.cs")]
    for project in maintained_csharp(ctx, "*.csproj"):
        if not (project.parent / "obj/project.assets.json").exists():
            raise ValueError(
                "Backend dependencies are missing; run uv run jpt bootstrap"
            )
        run(
            executable("dotnet"),
            "format",
            str(project),
            "--no-restore",
            *([] if fix else ["--verify-no-changes"]),
            "--include",
            *files,
            cwd=ctx.root,
        )


def format_target(ctx: Context, target: str, fix: bool) -> None:
    if target == "backend":
        dotnet_format(ctx, fix)
    else:
        patterns = (
            DOCS_FORMAT
            if target == "docs"
            else [
                ctx.config.frontend.directory + "/**/*.{ts,mjs,json,css,html}",
                ctx.config.browser.directory + "/**/*.{cjs,mjs,json}",
            ]
            if target == "frontend"
            else AUTOMATION_FORMAT
            if target == "automation"
            else [
                "scripts/**/*.{mjs,json}",
                "*.{json,mjs,yml,yaml}",
                "tests/tooling/**/*.{mjs,cjs,json}",
                "!renovate.json",
            ]
        )
        from jellyfin_plugin_toolkit.quality import prettier, python_format

        prettier(ctx.root, patterns, fix)
        if target == "tooling":
            roots = [
                name for name in ["scripts", "tests"] if (ctx.root / name).is_dir()
            ]
            if roots:
                python_format(ctx.root, fix, roots)
        if target in {"tooling", "automation"}:
            files = [
                p.name
                for p in sorted(ctx.root.glob("*.toml"))
                if p.name != "pyproject.toml"
                and (p.name == "plugin.toml") == (target == "automation")
            ]
            if not files:
                return
            run(
                executable("pnpm"),
                "exec",
                "taplo",
                "format",
                *([] if fix else ["--check"]),
                # uv owns pyproject.toml, including its native dependency-edit format.
                *files,
                cwd=ctx.root,
            )
