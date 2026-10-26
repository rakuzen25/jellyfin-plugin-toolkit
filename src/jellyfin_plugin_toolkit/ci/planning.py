"""Check scheduling and aggregation of executed and reused evidence."""

import os
import subprocess
import sys
import urllib.error
import zipfile
from datetime import UTC, datetime
from typing import Any

import jellyfin_plugin_toolkit.ci.evidence as ci_evidence
import jellyfin_plugin_toolkit.ci.policy as ci_policy
from jellyfin_plugin_toolkit.audit_policy import required as audit_required
from jellyfin_plugin_toolkit.common import artifacts
from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.provenance import record


def plan(
    ctx: Context, base: str, full: bool, release: bool, reuse: bool, scheduled: bool
) -> dict[str, Any]:
    root = ctx.root
    validation = ci_policy.ValidationPolicy.from_context(ctx)
    ci_policy.browser_inventory(ctx)
    ci_policy.check_classification(ctx, [])
    policy = ci_policy.policy_fingerprint(ctx)
    changed: list[str] = []
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    browser: dict[str, Any] = {}
    if (
        ctx.config.validation.browser_inputs
        and reuse
        and repository
        and os.environ.get("GH_TOKEN")
    ):
        try:
            browser = ci_evidence.browser_inputs(ctx, repository, policy)
        except urllib.error.URLError, KeyError, ValueError, zipfile.BadZipFile, OSError:
            print(
                "Browser provenance unavailable; selected assets will be built",
                file=sys.stderr,
            )
    inputs = ci_policy.fingerprints(ctx, browser or None)
    cumulative = (
        os.environ.get("GITHUB_EVENT_NAME") == "push" and not release and not scheduled
    )
    baseline: dict[str, Any] = {}
    comparison_mode = "push-baseline" if cumulative else "changed-inputs"
    fallback_reason = ""
    require_all = False
    if cumulative:
        try:
            if repository and os.environ.get("GH_TOKEN"):
                baseline = ci_evidence.validated_ancestor(
                    ctx, repository, os.environ.get("GITHUB_REF_NAME", ""), policy
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
    classified_changes = ci_policy.check_classification(ctx, changed)
    required = (
        set(validation.groups)
        if full or release or require_all
        else set[str]().union(*(ci_policy.impacts(ctx, p) for p in changed))
    )
    if baseline:
        required |= {
            g for g in validation.code if baseline.get("inputs", {}).get(g) != inputs[g]
        }
    proofs: dict[str, dict[str, Any]] = {}
    if reuse and (required or scheduled) and repository and os.environ.get("GH_TOKEN"):
        try:
            proofs = ci_evidence.evidence(ctx, repository, inputs, policy, scheduled)
        except (
            urllib.error.URLError,
            KeyError,
            ValueError,
            zipfile.BadZipFile,
            OSError,
        ):
            print("Evidence unavailable; required checks will run", file=sys.stderr)
    full_baseline: dict[str, Any] = {}
    if scheduled and not full:
        # All meaningful groups must match a full run, never merely a narrow pass.
        required = (
            set[str]()
            if all(g in proofs for g in validation.runtime)
            else set(validation.groups)
        )
        full = bool(required)
        if not full:
            # Carry authenticated input evidence forward across idle schedules.
            # It is code evidence only: no artifact/advisory gate is renewed.
            full_baseline = {
                "policy": policy,
                "inputs": {g: inputs[g] for g in validation.runtime},
                "origins": {g: proofs[g]["run_id"] for g in validation.runtime},
            }
            if "metadata" not in proofs:
                required.add("metadata")
        proofs = {}  # a safety run deliberately tests selector coverage
    elif full:
        proofs = {}  # explicit full always executes
    execute = required - proofs.keys()
    return {
        "schema": ci_policy.SCHEMA,
        "toolkit": record(root),
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
        "browser": browser,
        "force_execution": full or release or scheduled and bool(required),
        "reuse_allowed": reuse and not full and not release,
        "audit_required": audit_required(
            changed, fresh=release or scheduled, unknown=require_all or not base
        ),
        "changed": changed,
        "classified_changes": classified_changes,
        "required": sorted(required),
        "execute": sorted(execute),
        "reused": {g: proofs[g] for g in required if g in proofs},
        "full": full or execute == set(validation.groups),
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


def refine(ctx: Context, selection: dict[str, Any]) -> dict[str, Any]:
    """Resolve emitted-byte browser identity in the frontend job that owns the build."""
    from jellyfin_plugin_toolkit.ci import browser

    captured = browser.capture(ctx)
    selection["browser"] = captured
    selection["inputs"] = ci_policy.fingerprints(ctx, captured)
    precise = set(ctx.config.validation.browser_inputs).intersection(
        selection["required"]
    )
    if (
        precise
        and selection["reuse_allowed"]
        and not selection["force_execution"]
        and os.environ.get("GH_TOKEN")
    ):
        try:
            proofs = ci_evidence.evidence(
                ctx, selection["repository"], selection["inputs"], selection["policy"]
            )
            selection["reused"].update(
                {group: proofs[group] for group in precise if group in proofs}
            )
        except urllib.error.URLError, KeyError, ValueError, zipfile.BadZipFile, OSError:
            print(
                "Refined evidence unavailable; selected browser tests will run",
                file=sys.stderr,
            )
    selection["execute"] = sorted(
        set(selection["required"]) - selection["reused"].keys()
    )
    return selection


def finish(
    ctx: Context, selection: dict[str, Any], jobs: dict[str, Any]
) -> dict[str, Any]:
    root = ctx.root
    validation = ci_policy.ValidationPolicy.from_context(ctx)
    quality = {
        name: "not_required"
        if name != "docs" and group not in selection["execute"]
        else jobs.get("quality", {}).get("outputs", {}).get(name, "missing")
        for name, group in [
            ("docs", ""),
            ("automation", "metadata"),
            ("tooling", "tooling"),
            ("tooling-tests", "tooling"),
        ]
    }
    results: dict[str, str] = {}
    for group in selection["required"]:
        if group in selection["reused"]:
            results[group] = "success"
        else:
            results[group] = (
                "success"
                if all(
                    jobs.get(job, {}).get("result") == "success"
                    for job in validation.aggregate_jobs[group]
                    if job != "advisories" or selection["audit_required"]
                )
                else "failure"
            )
            checks = (
                ["automation"]
                if group == "metadata"
                else ["tooling", "tooling-tests"]
                if group == "tooling"
                else []
            )
            if any(quality[check] != "success" for check in checks):
                results[group] = "failure"
    receipt = dict(
        selection,
        groups=results,
        advisories={
            "result": jobs.get("advisories", {}).get("result", "missing")
            if selection["audit_required"]
            else "not_required"
        },
        quality=quality,
        validated_at=datetime.now(UTC).isoformat(),
        artifacts=artifacts.artifact_hashes(root)
        if results.get("artifacts") == "success"
        else {},
    )
    successful = (
        all(value == "success" for value in results.values())
        and receipt["advisories"]["result"] in {"success", "not_required"}
        and jobs.get("quality", {}).get("result") == "success"
        and all(value in {"success", "not_required"} for value in quality.values())
    )
    cumulative = selection.get("comparison", {}).get("mode") == "push-baseline"
    complete = successful and (
        set(selection["required"]) == set(validation.groups)
        or cumulative
        and bool(selection.get("comparison", {}).get("baseline"))
    )
    receipt["coverage"] = {
        "complete": complete,
        "identity": ci_evidence.coverage_identity(receipt) if complete else "",
    }
    return receipt
