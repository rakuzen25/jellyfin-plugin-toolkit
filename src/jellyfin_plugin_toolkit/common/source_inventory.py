"""One bounded source inventory for clean builds, source archives and CI inputs."""

import os
from pathlib import Path
from typing import Sequence

from jellyfin_plugin_toolkit.context import Context

ROOTS = ("src", "docs", "frontend", "vendor", "scripts", "tests", ".github")
EXCLUDED = {
    "bin",
    "obj",
    ".git",
    "node_modules",
    "results",
    "__pycache__",
    ".build",
    "dist",
    ".venv",
}
CONFIGS = {
    "plugin.toml",
    "uv.lock",
    "version.txt",
    "GPL-3.0.txt",
    "LICENSE",
    "NOTICE",
    "Directory.Build.props",
    "Directory.Build.targets",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "global.json",
    "renovate.json",
    "release-please-config.json",
    ".release-please-manifest.json",
    "knip.json",
    "eslint.config.mjs",
    ".prettierrc.json",
    ".prettierrc.mjs",
    "prettier.config.mjs",
    ".prettierignore",
    "pyproject.toml",
    ".node-version",
    ".python-version",
    ".editorconfig",
    ".gitignore",
    ".dockerignore",
}


def maintained_file(root: Path, path: Path) -> Path:
    """Source capture never follows a symlink, even when its target stays inside."""
    relative = path.relative_to(root)
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("Source archive cannot follow symlinks: " + str(path))
    if not path.is_file():
        raise ValueError("Missing maintained source file: " + str(relative))
    return path.resolve().relative_to(root)


def source_files(root: Path) -> list[Path]:
    root = root.resolve()
    ctx = Context.load(root)
    files = []
    for directory in ctx.config.source.roots:
        base = ctx.path(directory)
        for parent in [base, *base.parents]:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError("Source archive cannot follow symlinks: " + str(base))
        if base.exists() and not base.is_dir():
            raise ValueError("Source root must be a directory: " + directory)
        for current, directories, names in os.walk(base, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if name not in EXCLUDED and not name.startswith("results-")
            ]
            for name in directories:
                if (Path(current) / name).is_symlink():
                    raise ValueError(
                        "Source archive cannot follow symlinks: "
                        + str(Path(current) / name)
                    )
            for name in names:
                if name.startswith(("core.", ".env")) or name.endswith(
                    (".session.json", ".dmp")
                ):
                    continue
                files.append(maintained_file(root, Path(current) / name))
    files.extend(maintained_file(root, p) for p in root.glob("*.md"))
    files.extend(
        maintained_file(root, root / name)
        for name in CONFIGS
        if (root / name).exists() or (root / name).is_symlink()
    )
    files.extend(
        maintained_file(root, ctx.path(name)) for name in ctx.config.source.files
    )
    for key in ("notices", "web_notices"):
        for entry in getattr(ctx.config.distribution, key):
            files.append(maintained_file(root, ctx.path(entry.source)))
    return sorted(set(files))


def frontend_inputs(ctx: Context, maintained: Sequence[Path]) -> list[Path]:
    """Exact declared frontend sources and toolchain inputs, including external assets."""
    frontend = ctx.config.frontend
    directory = frontend.directory
    roots = [directory, frontend.assets or directory + "/assets"]
    explicit = []
    if frontend.enabled:
        if frontend.build:
            explicit.append(frontend.build)
        explicit.extend(str(Path(directory) / name) for name in frontend.entries)
        if frontend.admin_entry:
            explicit.append(str(Path(directory) / frontend.admin_entry))
        explicit.extend(
            getattr(frontend, key)
            for key in ("admin_template", "admin_styles", "boundary_check")
            if getattr(frontend, key)
        )
        explicit.extend(entry.source for entry in frontend.extra_assets)
    selected = {
        p
        for p in maintained
        if any(p.is_relative_to(Path(base)) for base in roots)
        or p.as_posix()
        in {
            "plugin.toml",
            "uv.lock",
            "version.txt",
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            ".node-version",
            ".python-version",
        }
    }
    for name in explicit:
        path = maintained_file(ctx.root, ctx.path(name))
        if path not in maintained:
            raise ValueError(
                "Frontend input is outside maintained source inventory: " + name
            )
        selected.add(path)
    for base in roots:
        directory_path = ctx.path(base)
        if directory_path.is_dir():
            for path in directory_path.rglob("*"):
                if any(
                    part in EXCLUDED for part in path.relative_to(directory_path).parts
                ):
                    continue
                if path.is_symlink():
                    raise ValueError(
                        "Frontend source cannot follow symlinks: " + str(path)
                    )
                if path.is_file() and path.relative_to(ctx.root) not in maintained:
                    raise ValueError(
                        "Frontend input is outside maintained source inventory: "
                        + str(path)
                    )
    return sorted(selected)
