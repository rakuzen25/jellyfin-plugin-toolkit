"""Archive, workflow, and preset updates must stay atomic."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            lock = {"repository": "example/toolkit", "version": "v1.0.0"}
            (root / "toolkit.lock.json").write_text(json.dumps(lock))
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            for name, text in render("example/toolkit", "a" * 40).items():
                (workflows / name).write_text(text)
            preset = root / "renovate.json"
            preset.write_text(
                json.dumps({"extends": ["github>example/toolkit#v1.0.0"]})
            )
            with patch.object(pins, "TOOLKIT_ROOT", toolkit):
                self.assertEqual(metadata, pins.check_pins(root, release=True))
                workflow = workflows / "validate.yml"
                original = workflow.read_text()
                workflow.write_text(original.replace("a" * 40, "b" * 40))
                with self.assertRaisesRegex(ValueError, "pin mismatch"):
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
        workflow = render("example/toolkit", "a" * 40)["validate.yml"]
        self.assertNotIn("secrets:", workflow)
        self.assertNotIn("write", workflow)
        self.assertIn("@" + "a" * 40, workflow)

    def test_mutable_revision_and_invalid_catalog_rejected(self):
        with self.assertRaises(ValueError):
            render("example/toolkit", "main")
        with self.assertRaises(ValueError):
            render("example/toolkit", "a" * 40, "owner/repo/extra")
