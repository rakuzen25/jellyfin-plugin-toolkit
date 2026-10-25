"""Authenticate the regular CI run whose candidate is eligible for a toolkit draft."""

import io
import re
import time
import zipfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jellyfin_plugin_toolkit.ci.evidence import api
from jellyfin_plugin_toolkit.release.service import repository_name

WORKFLOW = ".github/workflows/ci.yml"
JOBS = {
    "candidate",
    "toolkit",
    "starter (backend)",
    "starter (administrator-override)",
    "advisories",
    "validation-evidence",
}


class Data(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class Repository(Data):
    full_name: str


class Run(Data):
    id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    head_sha: str
    head_branch: str
    event: str
    path: str
    status: str
    conclusion: str | None
    repository: Repository
    head_repository: Repository


class Runs(Data):
    total_count: int
    workflow_runs: list[Run]


class Job(Data):
    name: str
    conclusion: str | None
    run_attempt: int


class Jobs(Data):
    total_count: int
    jobs: list[Job]


class Artifact(Data):
    id: int = Field(gt=0)
    name: str
    expired: bool


class Artifacts(Data):
    total_count: int
    artifacts: list[Artifact]


class Receipt(Data):
    schema_version: int
    repository: str
    revision: str
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    results: dict[str, str]


class Selection(Data):
    run_id: int = Field(gt=0)
    artifact_id: int = Field(gt=0)


def record(
    directory: Path,
    repository: str,
    revision: str,
    run_id: int,
    run_attempt: int,
    artifact_id: int,
    results: dict[str, str],
) -> None:
    repository_name(repository)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Validation receipt requires an immutable revision")
    if results != dict.fromkeys(
        ("candidate", "toolkit", "starter", "advisories"), "success"
    ):
        raise ValueError("Every toolkit validation job must succeed")
    receipt = Receipt(
        schema_version=1,
        repository=repository,
        revision=revision,
        run_id=run_id,
        run_attempt=run_attempt,
        candidate_artifact_id=artifact_id,
        results=results,
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "validation.json").write_text(receipt.model_dump_json() + "\n")


def eligible(run: Run, repository: str, revision: str) -> bool:
    return (
        run.repository.full_name == repository
        and run.head_repository.full_name == repository
        and run.path == WORKFLOW
        and run.event == "push"
        and run.head_branch == "main"
        and run.head_sha == revision
    )


def candidate(run: Run, repository: str, revision: str) -> Selection | None:
    """Read a bounded receipt and match it to authenticated jobs and artifacts."""
    artifacts = Artifacts.model_validate(
        api(repository, f"/actions/runs/{run.id}/artifacts?per_page=100")
    )
    if artifacts.total_count != len(artifacts.artifacts):
        raise ValueError("Incomplete toolkit artifact inventory")
    receipts = [
        item
        for item in artifacts.artifacts
        if item.name == f"toolkit-validation-{run.id}-{run.run_attempt}"
        and not item.expired
    ]
    if not receipts:
        return None
    jobs = Jobs.model_validate(
        api(repository, f"/actions/runs/{run.id}/jobs?filter=latest&per_page=100")
    )
    if jobs.total_count != len(jobs.jobs):
        raise ValueError("Incomplete toolkit validation job inventory")
    by_name = {job.name: job for job in jobs.jobs}
    if (
        len(by_name) != len(jobs.jobs)
        or set(by_name) != JOBS
        or any(job.conclusion != "success" for job in jobs.jobs)
        or by_name["validation-evidence"].run_attempt != run.run_attempt
        or any(job.run_attempt > run.run_attempt for job in jobs.jobs)
    ):
        raise ValueError(
            "Toolkit validation jobs do not match the required successful suite"
        )
    if len(receipts) != 1:
        raise ValueError("Ambiguous toolkit validation receipt")
    payload = api(repository, f"/actions/artifacts/{receipts[0].id}/zip", binary=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if (
            len(entries) != 1
            or entries[0].filename != "validation.json"
            or entries[0].file_size > 65536
        ):
            raise ValueError("Invalid toolkit validation receipt archive")
        receipt = Receipt.model_validate_json(archive.read(entries[0]))
    if (
        receipt.schema_version != 1
        or receipt.repository != repository
        or receipt.revision != revision
        or receipt.run_id != run.id
        or receipt.run_attempt != run.run_attempt
        or receipt.results
        != dict.fromkeys(("candidate", "toolkit", "starter", "advisories"), "success")
    ):
        raise ValueError("Toolkit validation receipt identity differs")
    selected = [
        item for item in artifacts.artifacts if item.id == receipt.candidate_artifact_id
    ]
    if not selected or selected[0].expired:
        return None
    # A failed-job rerun may reuse the candidate built by an earlier attempt.
    expected = f"toolkit-candidate-{run.id}-{by_name['candidate'].run_attempt}"
    if len(selected) != 1 or selected[0].name != expected:
        raise ValueError(
            "Toolkit validation candidate differs from the tested artifact"
        )
    return Selection(run_id=run.id, artifact_id=selected[0].id)


def select(
    repository: str,
    revision: str,
    *,
    retry: bool = False,
    wait_seconds: int = 1200,
) -> Selection | None:
    repository_name(repository)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Validation selection requires an immutable revision")
    deadline = time.monotonic() + wait_seconds
    while True:
        inventory = Runs.model_validate(
            api(
                repository,
                f"/actions/workflows/ci.yml/runs?event=push&branch=main&head_sha={revision}&per_page=100",
            )
        )
        if inventory.total_count != len(inventory.workflow_runs):
            raise ValueError("Incomplete toolkit validation run inventory")
        runs = [
            run
            for run in inventory.workflow_runs
            if eligible(run, repository, revision)
        ]
        if runs:
            run = max(runs, key=lambda item: item.id)
            if run.status == "completed":
                if run.conclusion == "success":
                    selection = candidate(run, repository, revision)
                    if selection:
                        return selection
                    if retry:
                        return None
                    raise ValueError(
                        "Validated toolkit artifacts are missing or expired; retry release preparation manually"
                    )
                if run.conclusion == "cancelled" and retry:
                    return None
                raise ValueError(
                    f"Toolkit validation run {run.id} ended with {run.conclusion}; fix or rerun validation before retrying release preparation"
                )
        elif retry:
            return None
        if time.monotonic() >= deadline:
            raise ValueError(
                "Timed out waiting for regular toolkit CI for "
                + revision
                + "; inspect that run and retry release preparation"
            )
        print("Waiting for regular toolkit validation of " + revision, flush=True)
        time.sleep(min(15, max(0, deadline - time.monotonic())))
