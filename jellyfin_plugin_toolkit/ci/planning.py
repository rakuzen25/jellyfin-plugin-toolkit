"""Check scheduling and aggregation of executed and reused evidence."""

import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import jellyfin_plugin_toolkit.ci.evidence as ci_evidence
import jellyfin_plugin_toolkit.ci.policy as ci_policy
from jellyfin_plugin_toolkit.common import artifacts
from jellyfin_plugin_toolkit.common.source_inventory import source_files
from jellyfin_plugin_toolkit.context import Context


def plan(
    root: Path, base: str, full: bool, release: bool, reuse: bool, scheduled: bool
) -> dict:
    ci_policy.browser_inventory(root)
    ci_policy.check_classification(root, [])
    inputs = ci_policy.fingerprints(root)
    policy = artifacts.digest(
        root,
        [Path(p) for p in ci_policy.POLICY]
        + [
            p
            for p in source_files(root)
            if str(p).startswith((".github/workflows/", ".github/actions/"))
        ],
    )
    changed: list[str] = []
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    cumulative = (
        os.environ.get("GITHUB_EVENT_NAME") == "push" and not release and not scheduled
    )
    baseline: dict = {}
    comparison_mode = "push-baseline" if cumulative else "changed-inputs"
    fallback_reason = ""
    require_all = False
    if cumulative:
        try:
            if repository and os.environ.get("GH_TOKEN"):
                baseline = ci_evidence.validated_ancestor(
                    root, repository, os.environ.get("GITHUB_REF_NAME", ""), policy
                )
            base = baseline.get("commit", "")
            if not base:
                fallback_reason = "No trustworthy validated ancestor available"
                require_all = True
        except (
            urllib.error.URLError,
            KeyError,
            ValueError,
            zipfile.BadZipFile,
            OSError,
        ):
            base = ""
            require_all = True
            fallback_reason = "Baseline lookup unavailable"
    if base:
        try:
            changed = ci_policy.git(
                root,
                "diff",
                "--no-renames",
                "--name-only",
                "--diff-filter=ACDMRTUXB",
                base,
                "HEAD",
            ).splitlines()
        except subprocess.CalledProcessError:
            require_all = True
            fallback_reason = "Comparison history unavailable"
    elif not scheduled and not release:
        require_all = True
        fallback_reason = fallback_reason or "Comparison history unavailable"
    classified_changes = ci_policy.check_classification(root, changed)
    required = (
        set(ci_policy.GROUPS)
        if full or release or require_all
        else set().union(*(ci_policy.impacts(p, root) for p in changed))
    )
    if baseline:
        required |= {
            g for g in ci_policy.CODE if baseline.get("inputs", {}).get(g) != inputs[g]
        }
    proofs: dict[str, dict] = {}
    if reuse and (required or scheduled) and repository and os.environ.get("GH_TOKEN"):
        try:
            proofs = ci_evidence.evidence(repository, inputs, policy, scheduled)
        except (
            urllib.error.URLError,
            KeyError,
            ValueError,
            zipfile.BadZipFile,
            OSError,
        ):
            print("Evidence unavailable; required checks will run", file=sys.stderr)
    full_baseline = {}
    if scheduled:
        # All meaningful groups must match a full run, never merely a narrow pass.
        required = (
            set()
            if all(g in proofs for g in ci_policy.RUNTIME)
            else set(ci_policy.GROUPS)
        )
        full = bool(required)
        if not full:
            # Carry authenticated input evidence forward across idle schedules.
            # It is code evidence only: no artifact/advisory gate is renewed.
            full_baseline = {
                "policy": policy,
                "inputs": {g: inputs[g] for g in ci_policy.RUNTIME},
                "origins": {g: proofs[g]["run_id"] for g in ci_policy.RUNTIME},
            }
            if "metadata" not in proofs:
                required.add("metadata")
        proofs = {}  # a safety run deliberately tests selector coverage
    elif full:
        proofs = {}  # explicit full always executes
    execute = required - proofs.keys()
    return {
        "schema": ci_policy.SCHEMA,
        "toolkit": __import__("json").loads((root / "toolkit.lock.json").read_text()),
        "commit": ci_policy.git(root, "rev-parse", "HEAD"),
        "tree": ci_policy.git(root, "rev-parse", "HEAD^{tree}"),
        "workflow_sha": os.environ.get(
            "GITHUB_SHA", ci_policy.git(root, "rev-parse", "HEAD")
        ),
        "repository": repository,
        "run_head_sha": os.environ.get(
            "CI_RUN_HEAD_SHA",
            os.environ.get("GITHUB_SHA", ci_policy.git(root, "rev-parse", "HEAD")),
        ),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
        "policy": policy,
        "inputs": inputs,
        "changed": changed,
        "classified_changes": classified_changes,
        "required": sorted(required),
        "execute": sorted(execute),
        "reused": {g: proofs[g] for g in required if g in proofs},
        "full": full or execute == set(ci_policy.GROUPS),
        "release": release,
        "full_baseline": full_baseline,
        "comparison": {
            "mode": comparison_mode,
            "base": base,
            "baseline": baseline,
            "fallback_reason": fallback_reason,
        },
        "coverage": {"complete": False},
    }


def finish(root: Path, selection: dict, jobs: dict) -> dict:
    mapping = {
        "metadata": ("metadata",),
        "frontend": ("frontend",),
        "backend": ("backend",),
        "tooling": ("tooling",),
        "workflow": ("workflow",),
        "rendering": ("rendering",),
        "performance": ("performance",),
        "artifacts": ("package", "installation", "reproducibility", "advisories"),
    }
    results = {}
    for group in selection["required"]:
        if group in selection["reused"]:
            results[group] = "success"
        else:
            results[group] = (
                "success"
                if all(
                    jobs.get(job, {}).get("result") == "success"
                    for job in (
                        Context.load(root)
                        .config["validation"]
                        .get("artifact_jobs", mapping["artifacts"])
                        if group == "artifacts"
                        else Context.load(root)
                        .config["validation"]
                        .get("aggregate_jobs", {})
                        .get(group, mapping.get(group, (group,)))
                    )
                )
                else "failure"
            )
    receipt = dict(
        selection,
        groups=results,
        validated_at=datetime.now(UTC).isoformat(),
        artifacts=artifacts.artifact_hashes(root)
        if results.get("artifacts") == "success"
        else {},
    )
    successful = all(value == "success" for value in results.values())
    cumulative = selection.get("comparison", {}).get("mode") == "push-baseline"
    complete = successful and (
        set(selection["required"]) == set(ci_policy.GROUPS)
        or cumulative
        and bool(selection.get("comparison", {}).get("baseline"))
    )
    receipt["coverage"] = {
        "complete": complete,
        "identity": ci_evidence.coverage_identity(receipt) if complete else "",
    }
    return receipt
