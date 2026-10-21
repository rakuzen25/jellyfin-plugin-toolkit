import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jellyfin_plugin_toolkit import bindings


class BindingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "pyproject.toml").write_text("before")

    def test_transaction_rejects_concurrent_edits(self):
        change = bindings.change_for(self.root, "pyproject.toml", b"after")
        (self.root / "pyproject.toml").write_text("user edit")
        with self.assertRaises(ValueError):
            bindings.apply(self.root, [change])
        self.assertEqual("user edit", (self.root / "pyproject.toml").read_text())

    def test_failed_replacement_restores_originals(self):
        changes = [
            bindings.change_for(self.root, "pyproject.toml", b"after"),
            bindings.change_for(self.root, "uv.lock", b"new"),
        ]
        original = bindings.os.replace

        def fail(source, target):
            if str(source).endswith("/1"):
                raise OSError("injected")
            return original(source, target)

        with patch.object(bindings.os, "replace", side_effect=fail):
            with self.assertRaises(OSError):
                bindings.apply(self.root, changes)
        self.assertEqual("before", (self.root / "pyproject.toml").read_text())
        self.assertFalse((self.root / "uv.lock").exists())

    def test_apply_is_idempotent_and_preserves_unrelated_files(self):
        (self.root / "unrelated").write_text("keep")
        bindings.apply(
            self.root, [bindings.change_for(self.root, "pyproject.toml", b"after")]
        )
        self.assertIsNone(bindings.change_for(self.root, "pyproject.toml", b"after"))
        self.assertEqual("keep", (self.root / "unrelated").read_text())

    def test_quoted_workflow_reference_preserves_formatting(self):
        old = {
            "repository": "rakuzen25/jellyfin-plugin-toolkit",
            "version": "v0.2.0",
            "revision": "a" * 40,
        }
        new = dict(old, version="v0.3.0")
        text = (
            "  uses: 'rakuzen25/jellyfin-plugin-toolkit/.github/workflows/validate.yml@"
            + "a" * 40
            + "' # v0.2.0\r\n"
        )
        files = {
            ".github/workflows/validate.yml": b"workflow",
            "toolkit-release.json": json.dumps(
                {"version": "0.3.0", "revision": "b" * 40}
            ).encode(),
        }
        updated = bindings.rewrite_workflow(text, old, new, files)
        self.assertIn("b" * 40, updated)
        self.assertTrue(updated.endswith("\r\n"))

    def test_source_update_requires_canonical_github_wheel(self):
        (self.root / "pyproject.toml").write_text(
            '[tool.uv.sources]\njellyfin-plugin-toolkit = {url="https://attacker.invalid/tool.whl"}'
        )
        with self.assertRaises(ValueError):
            bindings.target(self.root)
