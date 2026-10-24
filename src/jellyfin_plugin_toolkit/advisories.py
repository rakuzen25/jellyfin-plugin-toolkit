"""Fresh native audits, published-fix decisions, and reviewed expiring waivers."""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from urllib.parse import urlparse

from jellyfin_plugin_toolkit.common.process import executable, run

GHSA = re.compile(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}")


def command_json(ctx, *args, finding_exit=False):
    # Configuration output can contain registry credentials: never echo it.
    result = subprocess.run(
        [str(arg) for arg in args], cwd=ctx.root, capture_output=True, text=True
    )
    if result.returncode not in ({0, 1} if finding_exit else {0}):
        raise ValueError(f"{args[0]} audit command failed (exit {result.returncode})")
    try:
        value = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError(f"{args[0]} returned invalid audit JSON") from error
    if not isinstance(value, dict) or value.get("error") or value.get("errors"):
        raise ValueError(f"{args[0]} returned an audit error")
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing or invalid " + label)
    return value


def finding(ecosystem, package, version, ghsa, severity, locations, fixes):
    if not GHSA.fullmatch(ghsa):
        raise ValueError("Unsupported advisory identifier: " + ghsa)
    if not isinstance(locations, list) or not locations:
        raise ValueError("Advisory has no locations")
    if not isinstance(fixes, list):
        raise ValueError("Unknown advisory fix status")
    return {
        "ecosystem": ecosystem,
        "package": text(package, "package"),
        "version": text(version, "installed version"),
        "ghsa": ghsa,
        "severity": text(severity, "severity"),
        "locations": [text(location, "location") for location in locations],
        "fix_status": "published" if fixes else "unavailable",
        "fixes": fixes,
    }


def node_findings(ctx, findings):
    config = command_json(ctx, executable("pnpm"), "config", "list", "--json")

    def exclusions(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                current = path + "." + key.lower().replace("-", "").replace("_", "")
                if child and (
                    "audit" in current
                    and "ignore" in current
                    or key.lower().replace("-", "").replace("_", "")
                    in {"ignoreunfixable", "ignoreregistryerrors"}
                ):
                    raise ValueError(
                        "Native pnpm audit exclusions are forbidden; use advisories.waivers"
                    )
                exclusions(child, current)

    exclusions(config)
    report = command_json(
        ctx,
        executable("pnpm"),
        "audit",
        "--json",
        "--audit-level",
        "low",
        finding_exit=True,
    )
    advisories = report.get("advisories")
    if not isinstance(advisories, dict) or not isinstance(report.get("metadata"), dict):
        raise ValueError("Unsupported pnpm audit report")
    counts = report["metadata"].get("vulnerabilities")
    if (
        not isinstance(counts, dict)
        or not counts
        or any(type(count) is not int or count < 0 for count in counts.values())
        or (any(counts.values()) and not advisories)
    ):
        raise ValueError("Incomplete pnpm vulnerability counts")
    for advisory in advisories.values():
        patched = advisory["patched_versions"]
        unpublished = advisory.get("patched_versions_unpublished")
        if unpublished is not None and not isinstance(unpublished, bool):
            raise ValueError("Invalid unpublished-fix metadata")
        if patched is None:
            if unpublished is False:
                raise ValueError("Contradictory pnpm fix metadata")
            fixes = []
        elif isinstance(patched, str) and patched.strip():
            if unpublished is True:
                fixes = []
            else:
                fixes = [patched]
        else:
            raise ValueError("Invalid pnpm patched-version metadata")
        entries = advisory["findings"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("pnpm advisory has no installed findings")
        for entry in entries:
            findings.append(
                finding(
                    "npm",
                    advisory["module_name"],
                    entry["version"],
                    advisory["github_advisory_id"],
                    advisory["severity"],
                    entry["paths"],
                    fixes,
                )
            )


def github_advisory(ghsa):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "jellyfin-plugin-toolkit",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        "https://api.github.com/advisories/" + ghsa, headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if (
        not isinstance(data, dict)
        or data.get("ghsa_id") != ghsa
        or data.get("withdrawn_at")
    ):
        raise ValueError("Unusable GitHub advisory metadata for " + ghsa)
    return data


def nuget_fixes(data, package):
    entries = data["vulnerabilities"]
    if not isinstance(entries, list):
        raise ValueError("Invalid GitHub vulnerabilities metadata")
    matching = [
        entry
        for entry in entries
        if entry["package"]["ecosystem"] == "nuget"
        and entry["package"]["name"].casefold() == package.casefold()
    ]
    if not matching:
        raise ValueError("Advisory does not identify NuGet package " + package)
    fixes = set()
    for entry in matching:
        patched = entry["first_patched_version"]
        if patched is not None:
            fixes.add(text(patched, "NuGet fixed version"))
    return sorted(fixes)


def nuget_findings(ctx, project, findings, cache):
    settings = command_json(
        ctx,
        executable("dotnet"),
        "msbuild",
        project,
        "-getItem:NuGetAuditSuppress",
        "-nologo",
    )
    if settings["Items"]["NuGetAuditSuppress"]:
        raise ValueError(
            "Native NuGet audit suppressions are forbidden; use advisories.waivers"
        )
    run(
        executable("dotnet"),
        "restore",
        project,
        "--locked-mode",
        "-m:1",
        "--force-evaluate",
        "-p:NuGetAudit=true",
        "-p:NuGetAuditMode=all",
        "-p:NuGetAuditLevel=low",
        "-p:WarningsNotAsErrors=NU1901%3BNU1902%3BNU1903%3BNU1904",
        "-p:WarningsAsErrors=NU1900%3BNU1905",
        cwd=ctx.root,
    )
    report = command_json(
        ctx,
        executable("dotnet"),
        "package",
        "list",
        "--project",
        project,
        "--vulnerable",
        "--include-transitive",
        "--no-restore",
        "--format",
        "json",
        "--output-version",
        "1",
    )
    if (
        report.get("version") != 1
        or not isinstance(report.get("projects"), list)
        or not report["projects"]
    ):
        raise ValueError("Unsupported NuGet audit report")
    for result in report["projects"]:
        for framework in result.get("frameworks", []):
            for entry in framework.get("topLevelPackages", []) + framework.get(
                "transitivePackages", []
            ):
                for advisory in entry.get("vulnerabilities", []):
                    url = urlparse(advisory["advisoryurl"])
                    ghsa = url.path.removeprefix("/advisories/")
                    if (
                        url.scheme != "https"
                        or url.netloc != "github.com"
                        or not GHSA.fullmatch(ghsa)
                    ):
                        raise ValueError("Unsupported NuGet advisory URL")
                    item = finding(
                        "nuget",
                        entry["id"],
                        entry["resolvedVersion"],
                        ghsa,
                        advisory["severity"],
                        [str(project) + ":" + framework["framework"]],
                        [],
                    )
                    item["fix_status"] = "unknown"
                    findings.append(item)
                    if ghsa not in cache:
                        try:
                            cache[ghsa] = github_advisory(ghsa)
                        except (ValueError, KeyError, TypeError, OSError) as error:
                            cache[ghsa] = error
                    if isinstance(cache[ghsa], Exception):
                        raise ValueError("Advisory metadata unavailable for " + ghsa)
                    item["fixes"] = nuget_fixes(cache[ghsa], entry["id"])
                    item["fix_status"] = "published" if item["fixes"] else "unavailable"


def scope(entry):
    package = (
        entry["package"].casefold()
        if entry["ecosystem"] == "nuget"
        else entry["package"]
    )
    return entry["ecosystem"], package, entry["version"], entry["ghsa"]


def evaluate(findings, waivers, now, *, fail_on_findings=True):
    unique = {}
    for entry in findings:
        if entry["fix_status"] not in {"published", "unavailable"}:
            raise ValueError("Unknown fix status for " + entry["ghsa"])
        key = scope(entry)
        if key in unique:
            previous = unique[key]
            if (previous["fix_status"], previous["fixes"]) != (
                entry["fix_status"],
                entry["fixes"],
            ):
                raise ValueError("Conflicting fix information for " + entry["ghsa"])
            previous["locations"] = sorted(
                set(previous["locations"] + entry["locations"])
            )
        else:
            unique[key] = dict(entry)
    warnings = []
    for waiver in waivers:
        if waiver.timestamp(waiver.expires_at) <= now:
            warnings.append("Expired waiver: " + "/".join(waiver.scope))
        elif waiver.scope not in unique:
            warnings.append("Unused waiver: " + "/".join(waiver.scope))
    for key, entry in unique.items():
        waiver = next((waiver for waiver in waivers if waiver.scope == key), None)
        active = waiver is not None and waiver.timestamp(waiver.expires_at) > now
        entry["waiver_status"] = "active" if active else "expired" if waiver else "none"
        entry["blocking"] = (
            fail_on_findings and entry["fix_status"] == "published" and not active
        )
        if waiver:
            entry["waiver"] = waiver.model_dump()
    return sorted(unique.values(), key=scope), warnings


def audit(ctx):
    now = datetime.now(UTC)
    report = {
        "schema": 1,
        "checked_at": now.isoformat(),
        "fail_on_findings": ctx.config.advisories.fail_on_findings,
        "findings": [],
        "warnings": [],
        "errors": [],
    }
    findings = []
    cache = {}
    actions = []
    if (ctx.root / "pnpm-lock.yaml").exists():
        actions.append(("Node dependency audit", lambda: node_findings(ctx, findings)))
    projects = list(
        dict.fromkeys(
            [*ctx.config.backend.projects.values(), *ctx.config.backend.tests]
        )
    )
    for project in projects:
        actions.append(
            (
                "NuGet audit: " + str(project),
                lambda project=project: nuget_findings(ctx, project, findings, cache),
            )
        )
    for label, action in actions:
        try:
            action()
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            IndexError,
            OSError,
            subprocess.SubprocessError,
        ) as error:
            # External tools and HTTP errors can contain credentials: use controlled diagnostics.
            message = (
                str(error) if isinstance(error, ValueError) else type(error).__name__
            )
            report["errors"].append(label + ": " + message)
    try:
        report["findings"], report["warnings"] = evaluate(
            findings,
            ctx.config.advisories.waivers,
            now,
            fail_on_findings=ctx.config.advisories.fail_on_findings,
        )
    except ValueError as error:
        report["findings"] = findings
        report["errors"].append(str(error))
    report["success"] = not report["errors"] and not any(
        entry.get("blocking") for entry in report["findings"]
    )
    destination = ctx.root / "dist/evidence/advisories.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    lines = ["Advisory audit: " + ("passed" if report["success"] else "failed")]
    for entry in report["findings"]:
        status = "ERROR" if entry.get("blocking") else "WARNING"
        lines.append(
            f"{status}: {entry['ghsa']} {entry['ecosystem']}:{entry['package']}@{entry['version']} "
            f"({entry['severity']}); fix={entry['fix_status']} {', '.join(entry['fixes'])}; "
            f"waiver={entry.get('waiver_status', 'unknown')}; locations={', '.join(entry['locations'])}"
        )
    lines += ["WARNING: " + warning for warning in report["warnings"]]
    lines += ["ERROR: " + error for error in report["errors"]]
    print("\n".join(lines), file=sys.stderr)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        # Escape raw report text inside a fenced block, including adversarial backticks.
        with open(summary, "a") as output:
            output.write(
                "\n### Dependency advisories\n\n"
                + "\n".join("    " + line for line in lines)
                + "\n"
            )
    if not report["success"]:
        raise ValueError("Advisory audit failed; inspect dist/evidence/advisories.json")
    return report


def audit_toolkit():
    """Apply the same gate to this Python/Node tooling repository itself."""
    import tomllib
    from pathlib import Path
    from types import SimpleNamespace

    from jellyfin_plugin_toolkit.configuration import Advisories

    root = Path.cwd()
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    policy = metadata.get("tool", {}).get("jpt", {}).get("advisories", {})
    return audit(
        SimpleNamespace(
            root=root,
            config=SimpleNamespace(
                backend=SimpleNamespace(projects={}, tests=[]),
                advisories=Advisories.model_validate(policy),
            ),
        )
    )
