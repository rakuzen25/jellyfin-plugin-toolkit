"""Consumer formatting without task orchestration dependencies."""

from jellyfin_plugin_toolkit.build.backend import maintained_csharp, reference_directory
from jellyfin_plugin_toolkit.common.process import executable, python_checks, run
from jellyfin_plugin_toolkit.dev.references import verify

FRONTEND_FORMAT = [
    "frontend/**/*.{ts,mjs,json,css,html}",
    "tests/browser/**/*.{cjs,mjs,json}",
]
DOCS_FORMAT = ["*.md", "docs/**/*.md"]


def dotnet_format(ctx, fix=False):
    if not verify(ctx, reference_directory(ctx)):
        raise ValueError("Verified references are missing; run ./plugin bootstrap")
    files = [str(p) for p in maintained_csharp(ctx, "*.cs")]
    for project in maintained_csharp(ctx, "*.csproj"):
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
            cwd=ctx.root,
        )


def format_target(ctx, target, fix):
    if target == "backend":
        dotnet_format(ctx, fix)
    else:
        patterns = (
            DOCS_FORMAT
            if target == "docs"
            else FRONTEND_FORMAT
            if target == "frontend"
            else [
                "scripts/**/*.{mjs,json}",
                "*.{json,mjs,yml,yaml}",
                "tests/tooling/**/*.{mjs,cjs,json}",
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
            cwd=ctx.root,
        )
        if target == "tooling":
            roots = [
                name for name in ["scripts", "tests"] if (ctx.root / name).is_dir()
            ]
            if roots:
                run(
                    python_checks(ctx),
                    "-m",
                    "ruff",
                    "format",
                    *([] if fix else ["--check"]),
                    *roots,
                    cwd=ctx.root,
                )
            run(
                executable("pnpm"),
                "exec",
                "taplo",
                "format",
                *([] if fix else ["--check"]),
                *[str(p.name) for p in sorted(ctx.root.glob("*.toml"))],
                cwd=ctx.root,
            )
