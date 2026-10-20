"""Archive, workflow, and preset updates must stay atomic."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_bindings import release

from jellyfin_plugin_toolkit import bindings
from jellyfin_plugin_toolkit.checks import pins
from jellyfin_plugin_toolkit.workflows import render


class WorkflowTests(unittest.TestCase):
    def test_partial_update_rejected_until_archive_workflow_and_preset_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            toolkit = root / "archive"
            toolkit.mkdir()
            metadata = {"schema": 1, "version": "1.0.0", "revision": "a" * 40}
            (toolkit / "toolkit-release.json").write_text(json.dumps(metadata))
            lock = release(root, "1.0.0", "a" * 40)
            (root / "toolkit.lock.json").write_text(json.dumps(lock))
            (root / "plugin").write_bytes(
                (bindings.TOOLKIT_ROOT / "bootstrap/plugin").read_bytes()
            )
            (root / "plugin").chmod(0o755)
            (root / "plugin.toml").write_text("# consumer configuration\n")
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            for name, text in render(
                "example/toolkit", "a" * 40, version="v1.0.0"
            ).items():
                (workflows / name).write_text(text)
            preset = root / "renovate.json"
            preset.write_text(
                json.dumps({"extends": ["github>example/toolkit#v1.0.0"]})
            )
            from types import SimpleNamespace

            with (
                patch.object(pins, "TOOLKIT_ROOT", toolkit),
                patch.object(bindings, "check_integrations") as integrations,
                patch(
                    "jellyfin_plugin_toolkit.context.Context.load",
                    return_value=SimpleNamespace(config={}),
                ),
            ):
                self.assertEqual(metadata, pins.check_pins(root, release=True))
                integrations.assert_called_once_with(root, {})
                workflow = workflows / "validate.yml"
                original = workflow.read_text()
                workflow.write_text(original.replace("a" * 40, "b" * 40))
                with self.assertRaisesRegex(ValueError, "pin mismatch"):
                    pins.check_pins(root)
                workflow.write_text(original)
                workflow.write_text(original.replace("# v1.0.0", "# v2.0.0"))
                with self.assertRaisesRegex(ValueError, "version comment"):
                    pins.check_pins(root)
                workflow.write_text(original)
                preset.write_text(
                    json.dumps({"extends": ["github>example/toolkit#v2.0.0"]})
                )
                with self.assertRaisesRegex(ValueError, "preset"):
                    pins.check_pins(root)
                preset.write_text(
                    json.dumps({"extends": ["github>example/toolkit#v1.0.0"]})
                )
                metadata["version"] = "2.0.0"
                (toolkit / "toolkit-release.json").write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, "versions differ"):
                    pins.check_pins(root)

    def test_validation_has_no_publication_credentials(self):
        workflow = render("example/toolkit", "a" * 40, version="v1.0.0")["validate.yml"]
        self.assertNotIn("secrets:", workflow)
        self.assertNotIn("write", workflow)
        self.assertIn("@" + "a" * 40, workflow)

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
