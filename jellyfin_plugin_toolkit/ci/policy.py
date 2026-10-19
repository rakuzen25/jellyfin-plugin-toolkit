"""Consumer-declared impact policy with conservative input and evidence fingerprints."""

import fnmatch
import json
import subprocess
from pathlib import Path

from jellyfin_plugin_toolkit.common.artifacts import digest
from jellyfin_plugin_toolkit.common.source_inventory import source_files
from jellyfin_plugin_toolkit.context import Context

CONTEXT = Context.load()
GROUPS = CONTEXT.groups
CODE = tuple(g for g in GROUPS if g != "artifacts")
RUNTIME = tuple(g for g in CODE if g != "metadata")
SCHEMA = 4
POLICY = ("plugin.toml", "toolkit.lock.json", "version.txt")
GROUP_JOB_NAMES = {
    g: set(CONTEXT.config.get("validation", {}).get("job_names", {}).get(g, [g]))
    for g in CODE
}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def metadata_path(path):
    return (
        path == "renovate.json"
        or path == ".github/CODEOWNERS"
        or path.startswith(
            (".github/ISSUE_TEMPLATE/", ".github/PULL_REQUEST_TEMPLATE/")
        )
    )


def classification(path, root=None):
    ctx = Context.load(root) if root else CONTEXT
    rules = ctx.config["validation"].get("rules", [])
    selected = set()
    matched = []
    for rule in rules:
        if any(fnmatch.fnmatchcase(path, p) for p in rule["patterns"]):
            matched.append(rule.get("name", "configured"))
            selected.update(rule["groups"])
    if not matched:
        raise ValueError("Unclassified CI input: " + path)
    if selected - set(ctx.groups):
        raise ValueError("Impact rule names a disabled group: " + path)
    return "+".join(matched), selected


def impacts(path, root=None):
    return classification(path, root)[1]


def tracked_files(root):
    if not (root / ".git").exists():
        return [str(p) for p in source_files(root)]
    return (
        subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], text=True)
        .rstrip("\0")
        .split("\0")
    )


def check_classification(root, changed):
    unknown = []
    for path in sorted(set(tracked_files(root)) | set(changed)):
        if not path:
            continue
        try:
            classification(path, root)
        except ValueError:
            unknown.append(path)
    if unknown:
        raise ValueError("Unclassified CI inputs:\n" + "\n".join(unknown))
    return [
        {
            "path": p,
            "rule": classification(p, root)[0],
            "groups": sorted(impacts(p, root)),
        }
        for p in changed
    ]


def browser_inventory(root):
    ctx = Context.load(root)
    browser = ctx.config.get("browser", {})
    if not browser.get("groups"):
        return
    groups = json.loads(ctx.path(browser["groups"]).read_text())
    entries = [name for names in groups.values() for name in names]
    directory = ctx.path(browser.get("directory", "tests/browser"))
    actual = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*.spec.cjs")
        if not any(
            part.startswith("results") or part == "node_modules" for part in p.parts
        )
    }
    if len(entries) != len(set(entries)) or set(entries) != actual:
        raise ValueError("Every browser spec must belong to exactly one group")


def fingerprints(root):
    ctx = Context.load(root)
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
            "requirements-check.txt",
            "Directory.Build.props",
        }
    }
    classified = {p: impacts(str(p), root) for p in files}
    return {
        g: digest(
            root,
            [p for p in files if g == "artifacts" or p in shared or g in classified[p]],
            g != "artifacts",
        )
        for g in ctx.groups
    }


def metadata_check(root):
    for name in tracked_files(root):
        if metadata_path(name) and name.endswith(".json") and (root / name).exists():
            json.loads((root / name).read_text())
    from jellyfin_plugin_toolkit.checks.pins import check_pins
    from jellyfin_plugin_toolkit.common.process import IMAGES, executable, run

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
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        str(root) + ":/work:ro",
        "-w",
        "/work",
        IMAGES["actionlint"],
    )
    print("PASS: configured automation metadata syntax and workflow/toolkit pins")
