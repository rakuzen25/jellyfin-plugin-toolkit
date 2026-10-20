"""CLI for conservative check selection and authenticated validation evidence."""

import argparse
import json
import os
from pathlib import Path

import jellyfin_plugin_toolkit.ci.evidence as ci_evidence
import jellyfin_plugin_toolkit.ci.planning as ci_planning
import jellyfin_plugin_toolkit.ci.policy as ci_policy
from jellyfin_plugin_toolkit.common import artifacts
from jellyfin_plugin_toolkit.common.artifacts import verify_web
from jellyfin_plugin_toolkit.context import Context


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "command",
        choices=(
            "plan",
            "finish",
            "verify",
            "web-record",
            "web-verify",
            "metadata-check",
            "browser-verify",
        ),
    )
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--base", default="")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--group")
    parser.add_argument("--project")
    parser.add_argument("--workers", type=int)
    args = parser.parse_args(argv)
    ctx = Context.load(args.root)
    root = ctx.root
    from jellyfin_plugin_toolkit.checks.pins import check_pins

    check_pins(root)
    ctx.generated()
    if args.command == "metadata-check":
        ci_policy.metadata_check(ctx)
    elif args.command == "browser-verify":
        from jellyfin_plugin_toolkit.ci.browser import verify_execution

        verify_execution(
            ctx,
            json.loads((root / "dist/plan.json").read_text()),
            args.group,
            args.project,
            args.workers,
        )
    elif args.command == "plan":
        try:
            result = ci_planning.plan(
                ctx, args.base, args.full, args.release, args.reuse, args.scheduled
            )
        except ValueError as error:
            if output := os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(output, "a") as stream:
                    stream.write(
                        "### Validation selection failed\n\n```text\n"
                        + str(error)
                        + "\n```\n"
                    )
            raise SystemExit(str(error)) from error
        (root / "dist").mkdir(exist_ok=True)
        (root / "dist/plan.json").write_text(json.dumps(result, indent=2) + "\n")
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a") as stream:
                stream.writelines(
                    f"{group}={str(group in result['execute']).lower()}\n"
                    for group in ctx.groups
                )
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
                stream.write(
                    "### Validation impact\n\n" + summary.replace("\n", "\n\n")
                )
    elif args.command == "finish":
        result = ci_planning.finish(
            ctx,
            json.loads((root / "dist/plan.json").read_text()),
            json.loads(os.environ["CI_JOB_RESULTS"]),
        )
        (root / "dist/validation.json").write_text(json.dumps(result, indent=2) + "\n")
        if any(value != "success" for value in result["groups"].values()):
            raise SystemExit("Required validation groups failed")
    elif args.command == "verify":
        receipt = json.loads((root / "dist/validation.json").read_text())
        ci_evidence.verify_run(os.environ["GITHUB_REPOSITORY"], args.run_id, receipt)
        ci_evidence.verify(ctx, receipt, os.environ["GITHUB_REPOSITORY"], args.run_id)
    else:
        path = root / "dist/web-build.json"
        current = artifacts.web_inputs(root)
        if args.command == "web-record":
            if not all(
                name in current["files"] for name in ctx.config.frontend.required_assets
            ):
                raise ValueError("Prepared web assets are incomplete")
            path.write_text(json.dumps(current, indent=2) + "\n")
        else:
            verify_web(root)


if __name__ == "__main__":
    main()
