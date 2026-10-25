"""Archive, workflow, and preset updates must stay atomic."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.workflows import render


class WorkflowTests(unittest.TestCase):
    def test_metadata_edits_do_not_emit_or_cancel_code_check_contexts(self) -> None:
        caller = render("example/toolkit", "a" * 40, version="v1.0.0")["validate.yml"]
        self.assertIn("types: [opened, synchronize, reopened, edited]", caller)
        self.assertIn("'metadata-event' || 'validation'", caller)
        self.assertIn("github.event.changes.base != null", caller)
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
        ).read_text()
        self.assertIn("format('pr-{0}'", workflow)
        self.assertIn("'metadata-event-toolkit' || 'toolkit'", workflow)
        self.assertIn("label: administrator-override", workflow)

    def test_title_policy_needs_no_checkout_dependencies_or_write_token(self) -> None:
        caller = render("example/toolkit", "a" * 40, version="v1.0.0")["pr-title.yml"]
        self.assertIn("pr-title.yml@" + "a" * 40, caller)
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/pr-title.yml"
        ).read_text()
        self.assertNotIn("checkout", workflow)
        self.assertNotIn("write", workflow)
        self.assertIn("PR_TITLE: ${{ github.event.pull_request.title }}", workflow)

    def test_native_uv_owns_python_and_cache_identity_in_every_python_job(self) -> None:
        import re

        root = Path(__file__).resolve().parents[1] / ".github/workflows"
        for path in root.glob("*.yml"):
            workflow = path.read_text()
            self.assertNotIn("actions/setup-python@", workflow, path.name)
            jobs = re.split(
                r"(?m)^  ([a-z][a-z-]*):\n", workflow.split("jobs:\n", 1)[1]
            )
            for name, job in zip(jobs[1::2], jobs[2::2], strict=True):
                if "uv sync" not in job:
                    continue
                with self.subTest(workflow=path.name, job=name):
                    before_sync = job.split("uv sync", 1)[0]
                    self.assertRegex(
                        before_sync, r"uses: astral-sh/setup-uv@[0-9a-f]{40}"
                    )
                    self.assertNotIn("python-version:", before_sync)
                    for pin in (".python-version", "pyproject.toml", "uv.lock"):
                        self.assertIn(pin, before_sync)
                    self.assertLess(
                        before_sync.index("uses: actions/checkout@"),
                        before_sync.index("uses: astral-sh/setup-uv@"),
                    )

    def test_validation_has_no_publication_credentials(self) -> None:
        workflow = render("example/toolkit", "a" * 40, version="v1.0.0")["validate.yml"]
        self.assertNotIn("secrets:", workflow)
        self.assertNotIn("write", workflow)
        self.assertIn("@" + "a" * 40, workflow)

    def test_final_toolkit_gate_runs_even_after_a_dependency_fails(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
        ).read_text()
        final = workflow.split("  validation-evidence:\n", 1)[1]
        self.assertIn("if: always() &&", final)
        self.assertIn("needs: [candidate, toolkit, starter, advisories]", final)
        self.assertIn("needs.starter.result", final)

    def test_advisory_job_is_fresh_even_when_artifacts_are_not_selected(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/validate.yml"
        ).read_text()
        advisory = workflow.split("  advisories:\n", 1)[1].split("  validate:\n", 1)[0]
        self.assertNotIn("outputs.artifacts", advisory)
        self.assertIn("GH_TOKEN: ${{ github.token }}", advisory)
        self.assertIn("jpt test advisories", advisory)
        self.assertIn("if: always()", advisory)
        self.assertNotIn("reuse", advisory)

    def test_release_build_can_verify_the_toolkit_tag_with_read_only_token(
        self,
    ) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/validate.yml"
        ).read_text()
        build = workflow.split(
            "      - name: Build clean candidate with verified browser assets\n", 1
        )[1].split("      - run:", 1)[0]
        self.assertIn("GH_TOKEN: ${{ github.token }}", build)
        self.assertIn("args+=(--release)", build)
        self.assertIn('jpt build "${args[@]}"', build)
        self.assertIn("  contents: read\n", workflow)
        self.assertNotIn("contents: write", workflow)

    def test_browser_jobs_forward_runner_options_after_separator(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/validate.yml"
        ).read_text()
        commands = [
            line.strip()
            for line in workflow.splitlines()
            if "CI_BROWSER_GROUP=" in line and "browser" in line
        ]
        self.assertEqual(len(commands), 3)
        for command in commands:
            self.assertIn("uv run --no-sync jpt test browser -- --project", command)
            self.assertIn("--workers=", command)

    def test_app_setup_caller_is_manual_only_and_default_branch_only(self) -> None:
        caller = render("example/toolkit", "a" * 40, version="v1.0.0")["app-setup.yml"]
        self.assertNotIn("push:", caller)
        self.assertNotIn("schedule:", caller)
        self.assertIn("workflow_dispatch:", caller)
        self.assertIn("github.event.repository.default_branch", caller)
        self.assertIn("setup-automation.yml@" + "a" * 40, caller)
        self.assertIn("retry_closed: true", caller)
        self.assertNotIn("app_slug", caller)
        self.assertNotIn("contents: write", caller)

    def test_shared_setup_authenticates_before_project_setup(self) -> None:
        root = Path(__file__).resolve().parents[1] / ".github/workflows"
        caller = (root / "app-setup.yml").read_text()
        self.assertNotIn("push:", caller)
        workflow = (root / "setup-automation.yml").read_text()
        self.assertLess(
            workflow.index("/.github/actions/app-token@"),
            workflow.index("actions/checkout@"),
        )
        self.assertIn("group: app-setup-${{ github.repository }}", workflow)
        self.assertIn("github.event.repository.default_branch", workflow)

    def test_mutable_revision_and_invalid_catalog_rejected(self) -> None:
        with self.assertRaises(ValueError):
            render("example/toolkit", "main", version="v1.0.0")
        with self.assertRaises(ValueError):
            render("example/toolkit", "a" * 40, "owner/repo/extra", version="v1.0.0")

    def test_source_distribution_workflows_do_not_require_git_metadata(self) -> None:
        from types import SimpleNamespace

        from jellyfin_plugin_toolkit.checks import workflows as checker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_directory = root / ".github/workflows"
            workflow_directory.mkdir(parents=True)
            (workflow_directory / "validate.yml").write_text("name: Fixture\n")
            context = SimpleNamespace(
                config=SimpleNamespace(
                    images={"actionlint": "fixture@sha256:" + "a" * 64}
                )
            )
            with (
                patch.object(Context, "load", return_value=context),
                patch.object(checker, "run") as run,
            ):
                checker.check_workflows(root)
            self.assertFalse((root / ".git").exists())
            self.assertEqual(".github/workflows/validate.yml", run.call_args.args[-1])
            self.assertIn(str(root) + ":/work:ro", run.call_args.args)
            self.assertNotIn("install", run.call_args.args)

    def test_native_actionlint_config_is_used_without_git_discovery(self) -> None:
        from jellyfin_plugin_toolkit.checks.workflows import check_workflows

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/validate.yml").write_text("name: Fixture\n")
            (root / ".github/actionlint.yaml").write_text(
                "self-hosted-runner:\n  labels: [ubuntu-26.04]\n"
            )
            with patch("jellyfin_plugin_toolkit.checks.workflows.run") as run:
                check_workflows(root, "fixture@sha256:" + "a" * 64)
            self.assertIn("-config-file", run.call_args.args)
            self.assertIn(".github/actionlint.yaml", run.call_args.args)

    def test_title_policy_accepts_and_rejects_representative_titles(self) -> None:
        import os
        import subprocess
        import textwrap

        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/pr-title.yml"
        ).read_text()
        script = textwrap.dedent(workflow.split("        run: |\n", 1)[1])
        for title, code in [
            ("fix(ci): reuse validation", 0),
            ("feat!: change commands", 0),
            ("chore(deps): update dependencies", 0),
            ("Update dependencies", 1),
            ("fix: ", 1),
            ("feat(scope):missing space", 1),
        ]:
            with self.subTest(title=title):
                result = subprocess.run(
                    ["bash", "-e", "-c", script],
                    env={**os.environ, "PR_TITLE": title},
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(code, result.returncode)


class OptionalSyncWorkflowTests(unittest.TestCase):
    def test_consumer_caller_is_opt_in_and_handles_failed_validation(self) -> None:
        from jellyfin_plugin_toolkit.workflows import render_sync

        caller = render_sync(
            "example/toolkit",
            "a" * 40,
            version="v1.0.0",
            validation_workflow="Consumer checks",
        )
        self.assertIn('workflows: ["Consumer checks"]', caller)
        self.assertIn("types: [completed]", caller)
        self.assertNotIn("conclusion", caller)
        self.assertIn("sync-bindings.yml@" + "a" * 40, caller)
        self.assertNotIn("contents: write", caller)
