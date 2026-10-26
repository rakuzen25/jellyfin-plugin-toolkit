"""Authenticated GitHub evidence, receipt validation and reuse trust rules."""

from collections.abc import Iterator
from http.client import HTTPMessage
from typing import IO, TYPE_CHECKING, Any, cast

from jellyfin_plugin_toolkit.common.json_shapes import is_json_array, is_json_object

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context

import hashlib
import io
import json
import os
import re
import subprocess
import urllib.request
import zipfile
from datetime import UTC, datetime
from urllib.parse import urlparse

import jellyfin_plugin_toolkit.ci.policy as ci_policy
from jellyfin_plugin_toolkit.common import artifacts
from jellyfin_plugin_toolkit.provenance import record

WORKFLOWS = frozenset(
    {
        ".github/workflows/validate.yml",
        ".github/workflows/release-please.yml",
        ".github/workflows/release.yml",
        ".github/workflows/safety.yml",
    }
)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the Actions token to the signed artifact-storage origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes] | None,
        code: int,
        msg: str,
        headers: HTTPMessage | dict[str, str],
        newurl: str,
    ) -> urllib.request.Request | None:
        if urlparse(newurl).scheme != "https":
            raise ValueError("Artifact redirect must use HTTPS")
        request = super().redirect_request(
            req, cast(IO[bytes], fp), code, msg, cast(HTTPMessage, headers), newurl
        )
        if request and urlparse(req.full_url).netloc != urlparse(newurl).netloc:
            request.remove_header("Authorization")
        return request


def api(repository: str, endpoint: str, binary: bool = False) -> Any:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid repository identity")
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository + endpoint,
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.build_opener(SafeRedirect()).open(
        request, timeout=30
    ) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Evidence response exceeds size limit")
    return data if binary else json.loads(data)


def trusted(run: dict[str, Any], repository: str) -> bool:
    return (
        (run.get("repository") or dict[str, Any]()).get("full_name") == repository
        and (run.get("head_repository") or dict[str, Any]()).get("full_name")
        == repository
        and run.get("event")
        in {"push", "workflow_dispatch", "schedule", "pull_request"}
        and (
            (
                run.get("event") == "pull_request"
                and run.get("path") == ".github/workflows/validate.yml"
            )
            or run.get("head_branch") == "main"
            or str(run.get("head_branch", "")).startswith("release-please--")
            or (
                run.get("event") == "workflow_dispatch"
                and run.get("path") == ".github/workflows/release.yml"
                and re.fullmatch(r"v\d+\.\d+\.\d+", str(run.get("head_branch", "")))
                is not None
            )
        )
    )


def merged_pull_request(run: dict[str, Any], repository: str) -> bool:
    if run.get("event") != "pull_request":
        return True
    requests = run.get("pull_requests", [])
    if len(requests) != 1:
        return False
    request = api(repository, f"/pulls/{requests[0]['number']}")
    return (
        request.get("merged") is True
        and (request.get("head", {}).get("repo") or dict[str, Any]()).get("full_name")
        == repository
        and request.get("head", {}).get("sha") == run.get("head_sha")
        and (request.get("base", {}).get("repo") or dict[str, Any]()).get("full_name")
        == repository
        and request.get("base", {}).get("ref") == "main"
    )


def valid_receipt(receipt: dict[str, Any]) -> bool:
    """Reject malformed evidence before trust decisions inspect nested fields."""
    if not is_json_object(receipt):
        return False
    if any(
        key in receipt and not isinstance(receipt[key], bool)
        for key in ("full", "release")
    ):
        return False
    for key in ("inputs", "groups", "artifacts"):
        value = receipt.get(key, {})
        if not is_json_object(value) or any(
            not isinstance(v, str) for v in value.values()
        ):
            return False
    required = receipt.get("required")
    if (
        not is_json_array(required)
        or any(not isinstance(g, str) for g in required)
        or len(set(required)) != len(required)
    ):
        return False
    for key in ("coverage", "reused", "full_baseline"):
        if not is_json_object(receipt.get(key, {})):
            return False
    for proof in receipt.get("reused", {}).values():
        if not is_json_object(proof) or not isinstance(proof.get("run_id"), str):
            return False
    baseline = receipt.get("full_baseline", {})
    for key in ("inputs", "origins"):
        value = baseline.get(key, {})
        if not is_json_object(value) or any(
            not isinstance(v, str) for v in value.values()
        ):
            return False
    attempt = receipt.get("run_attempt", 1)
    return (
        isinstance(attempt, int)
        and not isinstance(attempt, bool)
        and attempt > 0
        and isinstance(receipt.get("policy"), str)
    )


def read_receipt(repository: str, run: dict[str, Any]) -> dict[str, Any] | None:
    # No cache, fork or caller-controlled URL is an evidence source.
    if not trusted(run, repository) or not merged_pull_request(run, repository):
        return None
    artifacts = api(repository, f"/actions/runs/{run['id']}/artifacts?per_page=100")[
        "artifacts"
    ]
    matches = [
        a
        for a in artifacts
        if a["name"] == "plugin-validation-receipt" and not a["expired"]
    ]
    if len(matches) != 1:
        return None
    data = api(repository, f"/actions/artifacts/{matches[0]['id']}/zip", binary=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.getinfo("validation.json").file_size > 1_000_000:
            raise ValueError("Receipt exceeds size limit")
        if archive.namelist().count("validation.json") != 1:
            raise ValueError("Duplicate receipt member")
        receipt: dict[str, Any] = json.loads(archive.read("validation.json"))
    if not valid_receipt(receipt):
        return None
    if (
        receipt.get("schema") != ci_policy.SCHEMA
        or receipt.get("run_id") != str(run["id"])
        or receipt.get("repository") != repository
    ):
        return None
    if (
        not is_json_object(receipt.get("inputs"))
        or not is_json_object(receipt.get("groups"))
        or not is_json_array(receipt.get("required"))
        or not isinstance(receipt.get("policy"), str)
        or not is_json_object(receipt.get("coverage", {}))
    ):
        return None
    # head_sha is the orchestrating source; a receipt also records the checked tag.
    if receipt.get("run_head_sha") != run.get("head_sha"):
        return None
    if receipt.get("run_attempt", 1) != run.get("run_attempt", 1):
        return None
    return receipt


def recent_runs(repository: str) -> Iterator[dict[str, Any]]:
    """Bound lookup cost while allowing busy repositories to find older evidence."""
    for page in range(1, 4):
        runs = api(repository, f"/actions/runs?per_page=100&page={page}")[
            "workflow_runs"
        ]
        yield from runs
        if len(runs) < 100:
            break


def coverage_identity(receipt: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {key: receipt.get(key) for key in ("commit", "tree", "policy", "inputs")},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def validated_ancestor(
    ctx: Context, repository: str, branch: str, policy: str
) -> dict[str, Any]:
    validation = ci_policy.ValidationPolicy.from_context(ctx)
    root = ctx.root
    for run in recent_runs(repository):
        if (
            run.get("head_branch") != branch
            or run.get("status", "completed") != "completed"
            or run.get("conclusion") != "success"
            or run.get("path") not in WORKFLOWS
        ):
            continue
        receipt = read_receipt(repository, run)
        if (
            not receipt
            or receipt.get("schema") != ci_policy.SCHEMA
            or receipt.get("policy") != policy
        ):
            continue
        groups = receipt.get("groups", {})
        complete_full = receipt.get("full") and all(
            groups.get(g) == "success" for g in validation.groups
        )
        coverage = receipt.get("coverage", {})
        complete_cumulative = (
            receipt.get("schema") == ci_policy.SCHEMA
            and coverage.get("complete") is True
            and coverage.get("identity") == coverage_identity(receipt)
            and all(groups.get(g) == "success" for g in receipt.get("required", []))
        )
        if not (complete_full or complete_cumulative):
            continue
        commit = receipt.get("commit", "")
        if not re.fullmatch(r"[a-f0-9]{40}", commit):
            continue
        try:
            ci_policy.git(root, "merge-base", "--is-ancestor", commit, "HEAD")
        except subprocess.CalledProcessError:
            continue
        return {
            "commit": commit,
            "run_id": str(run["id"]),
            "identity": coverage_identity(receipt),
            "inputs": receipt.get("inputs", {}),
        }
    return {}


def completed_group_origin(
    ctx: Context,
    repository: str,
    run: dict[str, Any],
    receipt: dict[str, Any],
    group: str,
    policy: str,
    fingerprint: str,
    cache: dict[tuple[str, str], Any],
) -> str | None:
    """Resolve one direct origin and verify its authenticated original jobs."""
    validation = ci_policy.ValidationPolicy.from_context(ctx)
    identifier = str(run["id"])
    if (
        receipt.get("policy") != policy
        or receipt.get("inputs", {}).get(group) != fingerprint
    ):
        return None
    if receipt.get("groups", {}).get(group) not in {None, "success"}:
        return None
    origin = receipt.get("reused", {}).get(group, {}).get("run_id")
    baseline = receipt.get("full_baseline", {})
    if (
        not origin
        and receipt.get("groups", {}).get(group) is None
        and (
            baseline.get("policy") == policy
            and baseline.get("inputs", {}).get(group) == fingerprint
        )
    ):
        origin = baseline.get("origins", {}).get(group)
    if origin:
        origin = str(origin)
        if not re.fullmatch(r"[0-9]+", origin) or origin == identifier:
            return None
        key = ("receipt", origin)
        if key not in cache:
            source = api(repository, f"/actions/runs/{origin}")
            if source.get("path") not in WORKFLOWS:
                return None
            cache[key] = (source, read_receipt(repository, source))
        source, proof = cache[key]
        if (
            not proof
            or proof.get("policy") != policy
            or proof.get("inputs", {}).get(group) != fingerprint
            or proof.get("groups", {}).get(group) != "success"
            or group in proof.get("reused", {})
            or group in proof.get("full_baseline", {}).get("origins", {})
        ):
            return None
        run, receipt, identifier = source, proof, origin
    if receipt.get("groups", {}).get(group) != "success":
        return None
    key = ("jobs", identifier)
    if key not in cache:
        response = api(
            repository, f"/actions/runs/{identifier}/jobs?filter=latest&per_page=100"
        )
        cache[key] = (
            response["jobs"]
            if response.get("total_count", len(response["jobs"])) <= 100
            else []
        )
    expected = validation.job_names[group]
    matches = [
        job for job in cache[key] if job.get("name", "").split(" / ")[-1] in expected
    ]
    if len(matches) != len(expected) or {
        job["name"].split(" / ")[-1] for job in matches
    } != set(expected):
        return None
    if not all(
        job.get("status") == "completed"
        and job.get("conclusion") == "success"
        and job.get("run_attempt", 1) == run.get("run_attempt", 1)
        for job in matches
    ):
        return None
    if group in {"metadata", "tooling"} and expected == frozenset({"quality"}):
        checks = (
            {"Check automation"}
            if group == "metadata"
            else {"Check tooling", "Test tooling"}
        )
        completed = {
            step.get("name")
            for step in matches[0].get("steps", [])
            if step.get("conclusion") == "success"
        }
        if not checks <= completed:
            return None
    return identifier


def evidence(
    ctx: Context,
    repository: str,
    inputs: dict[str, str],
    policy: str,
    full_only: bool = False,
) -> dict[str, dict[str, Any]]:
    validation = ci_policy.ValidationPolicy.from_context(ctx)
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    cache: dict[tuple[str, str], Any] = {}
    runs = recent_runs(repository)
    for run in runs:  # newest first: a matching failure must block older green reuse
        if run.get("path") not in WORKFLOWS:
            continue
        receipt = read_receipt(repository, run)
        if not receipt:
            if (
                trusted(run, repository)
                and merged_pull_request(run, repository)
                and run.get("conclusion")
                in {
                    "failure",
                    "cancelled",
                    "timed_out",
                }
            ):
                break  # missing failure evidence cannot justify falling back to older green
            continue
        if receipt.get("policy") != policy:
            continue
        baseline = receipt.get("full_baseline", {})
        carried_full = (
            run.get("path") == ".github/workflows/safety.yml"
            and run.get("conclusion") == "success"
            and baseline.get("policy") == policy
            and baseline.get("inputs")
            == {g: receipt.get("inputs", {}).get(g) for g in validation.runtime}
            and set(baseline.get("origins", {})) == set(validation.runtime)
        )
        if full_only and not receipt.get("full") and not carried_full:
            continue
        for group in validation.code:
            browser = receipt.get("browser", {})
            if (
                group in ctx.config.validation.browser_inputs
                and receipt.get("groups", {}).get(group) == "failure"
                and (not is_json_object(browser) or group not in browser)
            ):
                seen.add(group)
                continue
            if group in seen or receipt.get("inputs", {}).get(group) != inputs[group]:
                continue
            status = receipt.get("groups", {}).get(group)
            if status is None and carried_full and group in validation.runtime:
                status = "success"
            if status not in {"success", "failure"}:
                continue
            seen.add(group)
            if status == "success" and (
                not full_only or run.get("conclusion") == "success"
            ):
                origin = completed_group_origin(
                    ctx, repository, run, receipt, group, policy, inputs[group], cache
                )
                if not origin:
                    continue
                result[group] = {
                    "run_id": origin,
                    "input": inputs[group],
                }
        if all(group in seen for group in validation.code):
            break
    return result


def browser_inputs(ctx: Context, repository: str, policy: str) -> dict[str, Any]:
    """Recover unchanged source provenance as data; never build during planning."""
    from jellyfin_plugin_toolkit.ci import browser

    runner = {
        key: os.environ.get(key, "")
        for key in ("ImageOS", "ImageVersion", "RUNNER_ARCH", "RUNNER_OS")
    }
    for run in recent_runs(repository):
        if run.get("path") not in WORKFLOWS:
            continue
        receipt = read_receipt(repository, run)
        if not receipt or receipt.get("policy") != policy:
            continue
        proof = receipt.get("browser", {})
        try:
            browser.verify(ctx, proof)
        except ValueError:
            continue
        if all(item["runner_image"] == runner for item in proof.values()):
            return dict(proof)
    return {}


def verify(ctx: Context, receipt: dict[str, Any], repository: str, run_id: str) -> None:
    validation = ci_policy.ValidationPolicy.from_context(ctx)
    root = ctx.root
    if not valid_receipt(receipt):
        raise ValueError("Malformed validation receipt")
    if (
        receipt.get("schema") != ci_policy.SCHEMA
        or receipt.get("repository") != repository
        or receipt.get("run_id") != run_id
        or receipt.get("commit") != ci_policy.git(root, "rev-parse", "HEAD")
        or receipt.get("tree") != ci_policy.git(root, "rev-parse", "HEAD^{tree}")
    ):
        raise ValueError(
            "Receipt does not match repository, run and checked release source"
        )
    if receipt.get("toolkit") != record(root, release=True):
        raise ValueError("Receipt toolkit wheel or Python lock provenance changed")
    if not receipt.get("release") or any(
        receipt.get("groups", {}).get(g) != "success" for g in validation.groups
    ):
        raise ValueError("Receipt lacks complete successful release gates")
    if receipt.get("inputs") != ci_policy.fingerprints(ctx, receipt.get("browser", {})):
        raise ValueError("Validation input fingerprints changed")
    if not receipt.get("artifacts") or receipt[
        "artifacts"
    ] != artifacts.artifact_hashes(root):
        raise ValueError("Tested artifact hashes do not match")


def verify_run(repository: str, run_id: str, receipt: dict[str, Any]) -> None:
    if not re.fullmatch(r"[0-9]+", run_id):
        raise ValueError("Validation run ID must be numeric")
    run = api(repository, f"/actions/runs/{run_id}")
    current = run_id == os.environ.get("GITHUB_RUN_ID")
    if (
        not trusted(run, repository)
        or run.get("event") == "pull_request"
        or run.get("path") not in WORKFLOWS
        or (
            not current
            and (
                run.get("status") != "completed"
                or run.get("conclusion") not in {"success", "failure"}
            )
        )
    ):
        raise ValueError("Validation run is not a trusted completed repository run")
    jobs = api(repository, f"/actions/runs/{run_id}/jobs?filter=latest&per_page=100")[
        "jobs"
    ]
    if receipt.get("run_attempt", 1) != run.get("run_attempt", 1):
        raise ValueError("Receipt does not match the latest run attempt")
    if not any(
        job["name"].split(" / ")[-1] == "validate"
        and job.get("status", "completed") == "completed"
        and job.get("conclusion") == "success"
        and job.get("run_attempt", 1) == run.get("run_attempt", 1)
        for job in jobs
    ):
        raise ValueError("Run has not completed the aggregate validation gate")
    if receipt.get("run_head_sha") != run.get("head_sha"):
        raise ValueError("Receipt does not identify the authenticated workflow source")
    # Upload-only retries remain fast, but old advisory observations are not fresh.
    try:
        observed = datetime.fromisoformat(receipt["validated_at"])
        if observed.tzinfo is None:
            raise ValueError("Evidence timestamp requires a timezone")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid evidence timestamp") from error
    age = (datetime.now(UTC) - observed).total_seconds()
    if age < 0 or age > 86400:
        raise ValueError(
            "Release evidence must be within the past 24 hours; rebuild with fresh advisory checks"
        )
