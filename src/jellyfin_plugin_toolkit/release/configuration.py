"""Canonical Release Please rules for maintained internal NuGet references."""

import copy
import difflib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jellyfin_plugin_toolkit.common.json_shapes import is_json_array, is_json_object
from jellyfin_plugin_toolkit.common.source_inventory import source_files
from jellyfin_plugin_toolkit.context import Context

FILENAME = "release-please-config.json"
PREFIX = "$['dependencies'][*][?(@.type === 'Project')]['dependencies'][?("
SUFFIX = ")]"


def selector(assemblies: Iterable[str]) -> str:
    return (
        PREFIX
        + " || ".join(
            "@property === " + json.dumps(name) for name in sorted(assemblies)
        )
        + SUFFIX
    )


def canonical_rule(rule: object) -> bool:
    if not is_json_object(rule) or set(rule) != {"type", "path", "jsonpath"}:
        return False
    expression = rule.get("jsonpath")
    if rule["type"] != "json" or not isinstance(expression, str):
        return False
    if not expression.startswith(PREFIX) or not expression.endswith(SUFFIX):
        return False
    names = re.findall(r'@property === ("(?:[^"\\]|\\.)*")', expression)
    return bool(names) and expression == selector({json.loads(name) for name in names})


def internal_references(
    ctx: Context,
) -> tuple[set[str], dict[str, list[tuple[str, str, str, str]]]]:
    """Return lockfile paths and their (framework, project, dependency, range) rows."""
    assemblies = set(ctx.config.backend.project_assemblies)
    assemblies.add(ctx.identity.assembly)
    locks: dict[str, list[tuple[str, str, str, str]]] = {}
    for name in source_files(ctx.root):
        if name.name != "packages.lock.json":
            continue
        try:
            data = json.loads((ctx.root / name).read_text())
            rows: list[tuple[str, str, str, str]] = []
            for framework, projects in data["dependencies"].items():
                for project, record in projects.items():
                    if record.get("type") != "Project":
                        continue
                    for dependency, value in record.get("dependencies", {}).items():
                        if dependency in assemblies:
                            if not isinstance(value, str):
                                raise ValueError(
                                    "Internal project range must be a string"
                                )
                            rows.append((framework, project, dependency, value))
            locks[name.as_posix()] = rows
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"Invalid NuGet lockfile {name}: {error}") from error
    return assemblies, locks


def generated_config(ctx: Context, data: object) -> dict[str, Any]:
    """Preserve consumer settings, replacing only canonical internal-reference rules."""
    if not is_json_object(data) or not is_json_object(data.get("packages")):
        raise ValueError(
            "Release configuration requires packages with a single '.' entry"
        )
    packages = data["packages"]
    if set(packages) != {"."} or not is_json_object(packages["."]):
        raise ValueError("Release configuration supports only a single '.' package")
    package = packages["."]
    if package.get("release-type", data.get("release-type")) != "simple":
        raise ValueError("Release configuration requires release-type 'simple'")
    rules = package.get("extra-files", [])
    if not is_json_array(rules):
        raise ValueError(
            "Release configuration packages['.'].extra-files must be an array"
        )
    assemblies, locks = internal_references(ctx)
    preserved: list[Any] = []
    for rule in rules:
        path = (
            rule
            if isinstance(rule, str)
            else rule.get("path")
            if is_json_object(rule)
            else None
        )
        if not isinstance(path, str) or not path:
            raise ValueError("Release configuration extra-files entries require a path")
        if Path(path).name == "packages.lock.json" and canonical_rule(rule):
            continue
        normalized = (ctx.root / path).resolve()
        if (
            normalized.is_relative_to(ctx.root)
            and normalized.relative_to(ctx.root).as_posix() in locks
        ):
            raise ValueError(
                f"Custom release rule overlaps maintained NuGet lockfile {path}; "
                "remove that extra-files entry, then run uv run jpt release config --apply"
            )
        preserved.append(rule)
    required = [
        {"type": "json", "path": path, "jsonpath": selector(assemblies)}
        for path, rows in sorted(locks.items())
        if rows
    ]
    result = copy.deepcopy(data)
    if preserved or required or "extra-files" in package:
        result["packages"]["."]["extra-files"] = preserved + required
    return result


def proposal(root: Path) -> tuple[str, str]:
    path = root / FILENAME
    try:
        before = path.read_text()
        data = json.loads(before)
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {FILENAME}: {error}") from error
    expected = generated_config(Context.load(root), data)
    after = json.dumps(expected, indent=4) + "\n" if expected != data else before
    return before, after


def check(root: Path) -> None:
    before, after = proposal(root)
    if before != after:
        raise ValueError(
            "Release update rules are incomplete or stale; "
            "preview uv run jpt release config, then run uv run jpt release config --apply"
        )


def configure(root: Path, *, apply: bool = False, verify: bool = False) -> None:
    before, after = proposal(root)
    if verify:
        check(root)
        print("Validated Release Please internal project update rules")
    elif before == after:
        print("Release Please internal project update rules are current")
    elif apply:
        (root / FILENAME).write_text(after)
        print("Updated " + FILENAME)
    else:
        print(
            "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=FILENAME,
                    tofile=FILENAME,
                )
            ),
            end="",
        )
