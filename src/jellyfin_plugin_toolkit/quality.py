"""Native development checks shared by plugins and standalone repositories."""

import json
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jellyfin_plugin_toolkit.common.process import executable, run
from jellyfin_plugin_toolkit.resources_api import prepare
from jellyfin_plugin_toolkit.typing_policy import check_policy

Check = Literal["python", "javascript", "typescript", "docs", "workflows", "renovate"]


class Quality(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    checks: list[Check]
    typescript_projects: list[str] = Field(default_factory=list)
    images: dict[str, str] = Field(default_factory=dict)
    toolchain_file: str | None = None
    workflow_ignores: list[str] = Field(default_factory=list)


def selected(root: Path) -> Quality:
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    settings = Quality.model_validate(
        metadata.get("tool", {}).get("jpt", {}).get("quality", {})
    )
    if len(settings.checks) != len(set(settings.checks)):
        raise ValueError("Quality checks must not be repeated")
    if settings.toolchain_file:
        from jellyfin_plugin_toolkit.context import relative

        values = json.loads(relative(root, settings.toolchain_file).read_text())
        for name in ("actionlint", "renovate"):
            value = values.get(name)
            if not isinstance(value, str) or not value:
                raise ValueError("Toolchain must declare " + name)
            settings.images[name] = value
    return settings


def python(root: Path) -> None:
    """Use each tool's native file selection and this environment's interpreter."""
    check_policy(root)
    metadata = tomllib.loads((root / "pyproject.toml").read_text())["tool"]
    for tool in ("ruff", "mypy", "basedpyright"):
        if tool != "ruff":
            paths = metadata[tool].get("files" if tool == "mypy" else "include", ["."])
            if not has_sources(root, paths):
                continue
        arguments = ["check", "."] if tool == "ruff" else []
        if tool == "basedpyright":
            arguments = ["--pythonpath", sys.executable]
        run(sys.executable, "-m", tool, *arguments, cwd=root)


def has_sources(root: Path, patterns: list[str]) -> bool:
    """Empty starters need no synthetic Python files to satisfy type checkers."""
    ignored = {".build", ".venv", "node_modules", "dist", "bin", "obj", "__pycache__"}
    from jellyfin_plugin_toolkit.context import relative

    for pattern in patterns:
        relative(root, pattern)
        matches = [root] if pattern in {".", "./"} else list(root.glob(pattern))
        if not matches:
            raise ValueError("Python check path does not exist: " + pattern)
        for match in matches:
            if ignored.intersection(match.relative_to(root).parts):
                continue
            if match.is_file():
                if match.suffix in {".py", ".pyi"}:
                    return True
                continue
            for _, directories, filenames in match.walk():
                directories[:] = [name for name in directories if name not in ignored]
                if any(Path(name).suffix in {".py", ".pyi"} for name in filenames):
                    return True
    return False


def javascript(root: Path) -> None:
    run(executable("pnpm"), "exec", "eslint", ".", "--max-warnings", "0", cwd=root)


def typescript(root: Path, projects: list[str]) -> None:
    from jellyfin_plugin_toolkit.context import relative

    for project in projects:
        path = relative(root, project)
        run(
            executable("pnpm"),
            "exec",
            "tsc",
            "--project",
            str(path),
            "--noEmit",
            cwd=root,
        )


def automation(
    root: Path,
    image: str,
    kind: Literal["workflows", "renovate"],
    ignores: list[str] | None = None,
) -> None:
    if not image or "@sha256:" not in image:
        raise ValueError("Automation validators require a digest-pinned image")
    if kind == "workflows":
        from jellyfin_plugin_toolkit.checks.workflows import check_workflows

        check_workflows(root, image, ignores or [])
    else:
        run(
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "renovate-config-validator",
            "-v",
            str(root) + ":/work:ro",
            image,
            "--strict",
            "/work/renovate.json",
            cwd=root,
        )


def python_format(root: Path, fix: bool, roots: list[str] | None = None) -> None:
    run(
        sys.executable,
        "-m",
        "ruff",
        "format",
        *([] if fix else ["--check"]),
        *(roots or ["."]),
        cwd=root,
    )


def formatting(
    root: Path, checks: list[Check], fix: bool, target: str | None = None
) -> None:
    if target not in (None, "tooling", "docs"):
        raise ValueError("Unknown quality target: " + target)
    if target != "docs" and "python" in checks:
        python_format(root, fix)
    patterns = (
        ["*.md", "docs/**/*.md"]
        if target == "docs"
        else ["**/*.{md,json,mjs,cjs,ts,css,html,yml,yaml}"]
    )
    prettier(root, patterns, fix)


def prettier(root: Path, patterns: list[str], fix: bool) -> None:
    run(
        executable("pnpm"),
        "exec",
        "prettier",
        "--write" if fix else "--check",
        "--no-error-on-unmatched-pattern",
        *patterns,
        "!.build/**",
        "!dist/**",
        "!node_modules/**",
        "!**/node_modules/**",
        cwd=root,
    )


def main(root: Path, *, fix: bool = False, target: str | None = None) -> None:
    settings = selected(root)
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    if "jellyfin-plugin-toolkit" in metadata.get("tool", {}).get("uv", {}).get(
        "sources", {}
    ):
        from jellyfin_plugin_toolkit.checks.pins import check_pins

        check_pins(root)
    prepare(root)
    formatting(root, settings.checks, fix, target)
    if fix:
        return
    for check in settings.checks:
        if (
            target == "docs"
            and check != "docs"
            or target == "tooling"
            and check == "docs"
        ):
            continue
        if check == "python":
            python(root)
        elif check == "javascript":
            javascript(root)
        elif check == "typescript":
            if not settings.typescript_projects:
                raise ValueError("TypeScript checks require native project paths")
            typescript(root, settings.typescript_projects)
        elif check == "docs":
            from jellyfin_plugin_toolkit.checks.docs import check as links

            errors = links(root)
            if errors:
                raise ValueError("\n".join(errors))
        else:
            image = settings.images.get(
                "actionlint" if check == "workflows" else "renovate", ""
            )
            automation(root, image, check, settings.workflow_ignores)
