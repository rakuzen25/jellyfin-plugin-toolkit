"""Framework-independent CI operations and receipt writing."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context

import json
import os
import shutil

import jellyfin_plugin_toolkit.ci.evidence as ci_evidence
import jellyfin_plugin_toolkit.ci.planning as ci_planning
from jellyfin_plugin_toolkit.common import artifacts


def plan(
    ctx: Context,
    *,
    base: str = "",
    full: bool = False,
    release: bool = False,
    reuse: bool = False,
    scheduled: bool = False,
) -> None:
    root = ctx.root
    try:
        result = ci_planning.plan(ctx, base, full, release, reuse, scheduled)
    except ValueError as error:
        if output := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(output, "a") as stream:
                stream.write(
                    "### Validation selection failed\n\n```text\n"
                    + str(error)
                    + "\n```\n"
                )
        raise ValueError(str(error)) from error
    (root / "dist").mkdir(exist_ok=True)
    (root / "dist/plan.json").write_text(json.dumps(result, indent=2) + "\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.writelines(
                f"{group}={str(group in result['execute']).lower()}\n"
                for group in ctx.groups
            )
            stream.write("audit=" + str(result["audit_required"]).lower() + "\n")
            stream.write(
                "web="
                + str(
                    ctx.config.frontend.enabled
                    and bool(
                        set(result["execute"])
                        & {
                            "frontend",
                            "backend",
                            "workflow",
                            "rendering",
                            "performance",
                            "artifacts",
                        }
                    )
                ).lower()
                + "\n"
            )
    summary = (
        "Comparison: "
        + result["comparison"]["mode"]
        + "\nBaseline: "
        + (result["comparison"]["base"] or "none")
        + "\nBaseline run: "
        + result["comparison"]["baseline"].get("run_id", "none")
        + "\nFallback: "
        + (result["comparison"]["fallback_reason"] or "none")
        + "\nSelected: "
        + ", ".join(result["execute"])
        + "\nReused: "
        + ", ".join(result["reused"])
        + "\nNot required: "
        + ", ".join(set(ctx.groups) - set(result["required"]))
        + "\n"
    )
    summary += "\nFile classifications:\n" + "\n".join(
        f"{entry['path']}: {entry['rule']} → {', '.join(entry['groups']) or 'no executable checks'}"
        for entry in result["classified_changes"]
    )
    print(summary)
    if output := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(output, "a") as stream:
            stream.write("### Validation impact\n\n" + summary.replace("\n", "\n\n"))


def finish(ctx: Context) -> None:
    root = ctx.root
    result = ci_planning.finish(
        ctx,
        json.loads((root / "dist/plan.json").read_text()),
        json.loads(os.environ["CI_JOB_RESULTS"]),
    )
    (root / "dist/validation.json").write_text(json.dumps(result, indent=2) + "\n")
    if (
        any(value != "success" for value in result["groups"].values())
        or result["advisories"]["result"] not in {"success", "not_required"}
        or any(
            value not in {"success", "not_required"}
            for value in result["quality"].values()
        )
    ):
        raise ValueError("Required validation groups failed")


def refine(ctx: Context) -> None:
    path = ctx.root / "dist/plan.json"
    result = ci_planning.refine(ctx, json.loads(path.read_text()))
    path.write_text(json.dumps(result, indent=2) + "\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.writelines(
                f"{group}={str(group in result['execute']).lower()}\n"
                for group in ("workflow", "rendering", "performance")
            )


def release_receipt(ctx: Context, run_id: str) -> None:
    """Authenticate internal evidence and stage the public release independently."""
    from jellyfin_plugin_toolkit.release.receipt import PublicReceipt

    root = ctx.root
    receipt = json.loads((root / "dist/validation.json").read_text())
    ci_evidence.verify_run(os.environ["GITHUB_REPOSITORY"], run_id, receipt)
    ci_evidence.verify(ctx, receipt, os.environ["GITHUB_REPOSITORY"], run_id)
    public = PublicReceipt.model_validate(
        {
            name: receipt[name]
            for name in PublicReceipt.model_fields
            if name != "schema_version"
        }
    )
    directory = root / "dist/release"
    directory.mkdir()
    for name in public.artifacts:
        if name != os.path.basename(name) or name == "validation.json":
            raise ValueError("Invalid public release artifact name")
        shutil.copyfile(root / "dist" / name, directory / name)
    (directory / "validation.json").write_text(
        public.model_dump_json(indent=2, by_alias=True) + "\n"
    )


def web_record(ctx: Context) -> None:
    root = ctx.root
    path = root / "dist/web-build.json"
    current = artifacts.web_inputs(root)
    if not all(
        name in current["files"] for name in ctx.config.frontend.required_assets
    ):
        raise ValueError("Prepared web assets are incomplete")
    path.write_text(json.dumps(current, indent=2) + "\n")
