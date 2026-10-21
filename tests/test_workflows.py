"""Archive, workflow, and preset updates must stay atomic."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jellyfin_plugin_toolkit.workflows import render


class WorkflowTests(unittest.TestCase):
    def test_validation_has_no_publication_credentials(self):
        workflow = render("example/toolkit", "a" * 40, version="v1.0.0")["validate.yml"]
        self.assertNotIn("secrets:", workflow)
        self.assertNotIn("write", workflow)
        self.assertIn("@" + "a" * 40, workflow)

    def test_app_setup_caller_is_always_generated_and_default_branch_only(self):
        caller = render("example/toolkit", "a" * 40, version="v1.0.0")["app-setup.yml"]
        self.assertIn("push:\n    branches: [main]\n    paths:", caller)
        for path in [
            "renovate.json",
            ".github/jpt-automation.json",
            ".github/workflows/app-setup.yml",
        ]:
            self.assertIn("      - " + path, caller)
        self.assertIn("workflow_dispatch:", caller)
        self.assertIn("github.event.repository.default_branch", caller)
        self.assertIn("setup-automation.yml@" + "a" * 40, caller)
        self.assertIn("retry_closed:", caller)
        self.assertNotIn("app_slug", caller)
        self.assertNotIn("contents: write", caller)

    def test_mutable_revision_and_invalid_catalog_rejected(self):
        with self.assertRaises(ValueError):
            render("example/toolkit", "main", version="v1.0.0")
        with self.assertRaises(ValueError):
            render("example/toolkit", "a" * 40, "owner/repo/extra", version="v1.0.0")

    def test_source_distribution_workflows_do_not_require_git_metadata(self):
        from types import SimpleNamespace

        from jellyfin_plugin_toolkit.checks import workflows as checker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            directory = root / ".github/workflows"
            directory.mkdir(parents=True)
            (directory / "validate.yml").write_text("name: Fixture\n")
            context = SimpleNamespace(
                config=SimpleNamespace(
                    images={"actionlint": "fixture@sha256:" + "a" * 64}
                )
            )
            with (
                patch.object(checker.Context, "load", return_value=context),
                patch.object(checker, "run") as run,
            ):
                checker.check_workflows(root)
            self.assertFalse((root / ".git").exists())
            self.assertEqual(".github/workflows/validate.yml", run.call_args.args[-1])
            self.assertIn(str(root) + ":/work:ro", run.call_args.args)
            self.assertNotIn("install", run.call_args.args)


class OptionalSyncWorkflowTests(unittest.TestCase):
    def test_consumer_caller_is_opt_in_and_handles_failed_validation(self):
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
