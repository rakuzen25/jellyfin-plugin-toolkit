"""One bounded source inventory for clean builds, source archives and CI inputs."""

import os
from pathlib import Path

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
    "plugin",
    "plugin.toml",
    "toolkit.lock.json",
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
    ".prettierignore",
    "pyproject.toml",
    "requirements-check.txt",
    ".node-version",
    ".python-version",
    ".editorconfig",
    ".gitignore",
    ".dockerignore",
}


def source_files(root):
    root = root.resolve()
    ctx = Context.load(root)
    files = []
    for directory in ctx.config.get("source", {}).get("roots", ROOTS):
        base = ctx.path(directory)
        for current, directories, names in os.walk(base, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if name not in EXCLUDED
                and not name.startswith("results-")
                and not (Path(current) / name).is_symlink()
            ]
            for name in names:
                path = Path(current) / name
                if name.startswith(("core.", ".env")) or name.endswith(
                    (".session.json", ".dmp")
                ):
                    continue
                if path.is_symlink():
                    raise ValueError(
                        "Source archive cannot follow symlinks: " + str(path)
                    )
                if path.is_file():
                    files.append(path.relative_to(root))
    files.extend(p.relative_to(root) for p in root.glob("*.md"))
    files.extend(Path(name) for name in CONFIGS if (root / name).is_file())
    files.extend(
        Path(name)
        for name in ctx.config.get("source", {}).get("files", [])
        if ctx.path(name).is_file()
    )
    for entry in ctx.config.get("distribution", {}).get("notices", []):
        path = ctx.path(entry["source"])
        if not path.is_file():
            raise ValueError("Missing source distribution notice: " + str(path))
        files.append(path.relative_to(root))
    return sorted(set(files))
