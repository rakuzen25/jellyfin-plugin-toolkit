"""Consumer-declared impact policy with conservative input and evidence fingerprints."""

import fnmatch
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from jellyfin_plugin_toolkit.common.artifacts import digest
from jellyfin_plugin_toolkit.common.source_inventory import source_files
from jellyfin_plugin_toolkit.provenance import identity

SCHEMA = 7
POLICY = ("plugin.toml", "uv.lock", "version.txt")
ARTIFACT_JOBS = ("package", "installation", "reproducibility", "advisories")


@dataclass(frozen=True)
class ValidationPolicy:
    groups: tuple[str, ...]
    code: tuple[str, ...]
    runtime: tuple[str, ...]
    job_names: Mapping[str, frozenset[str]]
    aggregate_jobs: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_context(cls, ctx):
        validation = ctx.config.validation
        groups = ctx.groups
        validation.valid_groups()
        code = tuple(g for g in groups if g != "artifacts")
        return cls(
            groups,
            code,
            tuple(g for g in code if g != "metadata"),
            MappingProxyType(
                {g: frozenset(validation.job_names.get(g, [g])) for g in code}
            ),
            MappingProxyType(
                {
                    g: tuple(
                        validation.artifact_jobs
                        if g == "artifacts"
                        else validation.aggregate_jobs.get(g, [g])
                    )
                    for g in groups
                }
            ),
        )


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def metadata_path(path):
    return (
        path in {"renovate.json", ".github/jpt-automation.json"}
        or path == ".github/CODEOWNERS"
        or path.startswith(
            (".github/ISSUE_TEMPLATE/", ".github/PULL_REQUEST_TEMPLATE/")
        )
    )


def classification(ctx, path):
    rules = ctx.config.validation.rules
    selected = set()
    matched = []
    for rule in rules:
        if any(fnmatch.fnmatchcase(path, p) for p in rule.patterns):
            matched.append(rule.name)
            selected.update(rule.groups)
    if not matched:
        raise ValueError("Unclassified CI input: " + path)
    if selected - set(ctx.groups):
        raise ValueError("Impact rule names a disabled group: " + path)
    return "+".join(matched), selected


def impacts(ctx, path):
    return classification(ctx, path)[1]


def tracked_files(root):
    if not (root / ".git").exists():
        return [str(p) for p in source_files(root)]
    return (
        subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], text=True)
        .rstrip("\0")
        .split("\0")
    )


def check_classification(ctx, changed):
    root = ctx.root
    unknown = []
    for path in sorted(set(tracked_files(root)) | set(changed)):
        if not path:
            continue
        try:
            classification(ctx, path)
        except ValueError:
            unknown.append(path)
    if unknown:
        raise ValueError("Unclassified CI inputs:\n" + "\n".join(unknown))
    return [
        {
            "path": p,
            "rule": classification(ctx, p)[0],
            "groups": sorted(impacts(ctx, p)),
        }
        for p in changed
    ]


def browser_inventory(ctx):
    browser = ctx.config.browser
    if not browser.groups:
        return
    groups = json.loads(ctx.path(browser.groups).read_text())
    entries = [name for names in groups.values() for name in names]
    directory = ctx.path(browser.directory)
    actual = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*.spec.cjs")
        if not any(
            part.startswith("results") or part == "node_modules" for part in p.parts
        )
    }
    if len(entries) != len(set(entries)) or set(entries) != actual:
        raise ValueError("Every browser spec must belong to exactly one group")


def fingerprints(ctx, browser=None):
    root = ctx.root
    files = source_files(root)
    shared = {Path(p) for p in POLICY} | {
        p
        for p in files
        if str(p).startswith(".github/")
        or str(p)
        in {
            ".python-version",
            ".node-version",
            "global.json",
            "pnpm-lock.yaml",
            "pyproject.toml",
            "uv.lock",
            "Directory.Build.props",
        }
    }
    classified = {p: impacts(ctx, str(p)) for p in files}
    result = {
        g: digest(
            root,
            [p for p in files if g == "artifacts" or p in shared or g in classified[p]],
            g != "artifacts",
        )
        for g in ctx.groups
    }
    if ctx.config.validation.browser_inputs:
        from jellyfin_plugin_toolkit.ci.browser import verify

        if browser is None:
            raise ValueError(
                "Precise browser fingerprints require built-asset evidence"
            )
        result.update(verify(ctx, browser))
    return result


def policy_fingerprint(ctx):
    from jellyfin_plugin_toolkit.ci.browser import fingerprint

    return fingerprint(
        {
            "schema": SCHEMA,
            "toolkit": identity(ctx.root),
            "workflows": digest(
                ctx.root,
                [
                    p
                    for p in source_files(ctx.root)
                    if p.as_posix().startswith(
                        (".github/workflows/", ".github/actions/")
                    )
                ],
            ),
            "groups": ctx.config.validation.groups,
            "jobs": ctx.config.validation.job_names,
            "aggregate": ctx.config.validation.aggregate_jobs,
            "artifacts": ctx.config.validation.artifact_jobs,
        }
    )


def metadata_check(ctx):
    root = ctx.root
    for name in tracked_files(root):
        if metadata_path(name) and name.endswith(".json") and (root / name).exists():
            json.loads((root / name).read_text())
    from jellyfin_plugin_toolkit.automation import (
        STATE_PATH,
        check_renovate,
        record_identity,
    )
    from jellyfin_plugin_toolkit.checks.pins import check_pins
    from jellyfin_plugin_toolkit.common.process import executable, run

    state = root / STATE_PATH
    if state.exists():
        check_renovate(root, record_identity(json.loads(state.read_text())))

    check_pins(root)
    run(
        executable("pnpm"),
        "exec",
        "taplo",
        "format",
        "--check",
        "plugin.toml",
        cwd=root,
    )
    from jellyfin_plugin_toolkit.checks.workflows import check_workflows

    check_workflows(root)
    print("PASS: configured automation metadata syntax and workflow/toolkit pins")
