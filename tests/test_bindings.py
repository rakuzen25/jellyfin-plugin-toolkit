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

    def test_named_preset_drift_stops_rewrite_and_unrelated_presets_are_preserved(self):
        old = {"repository": "rakuzen25/jellyfin-plugin-toolkit", "version": "v0.2.0"}
        new = dict(old, version="v0.3.0")
        config = '{"extends": ["github>example/policy#v9.0.0", "github>rakuzen25/jellyfin-plugin-toolkit:tooling#v0.1.0"]}'
        with self.assertRaisesRegex(ValueError, "preset drift"):
            bindings.rewrite_preset(config, old, new)
        config = config.replace(":tooling#v0.1.0", ":tooling#v0.2.0")
        updated = bindings.rewrite_preset(config, old, new)
        self.assertIn("github>example/policy#v9.0.0", updated)
        self.assertIn(":tooling#v0.3.0", updated)

    def test_source_update_requires_canonical_github_wheel(self):
        (self.root / "pyproject.toml").write_text(
            '[tool.uv.sources]\njellyfin-plugin-toolkit = {url="https://attacker.invalid/tool.whl"}'
        )
        with self.assertRaises(ValueError):
            bindings.target(self.root)


class ReleaseBindingPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.url = "https://github.com/rakuzen25/jellyfin-plugin-toolkit/releases/download/v0.3.0/jellyfin_plugin_toolkit-0.3.0-py3-none-any.whl"
        (self.root / "pyproject.toml").write_text(
            '[project]\nname="fixture"\ndependencies=["jellyfin-plugin-toolkit"]\n'
            '[tool.uv.sources]\njellyfin-plugin-toolkit={url="' + self.url + '"}\n'
        )
        self.old_lock = (
            'version=1\n[[package]]\nname="jellyfin-plugin-toolkit"\nversion="0.2.0"\n'
            'source={url="' + self.url.replace("0.3.0", "0.2.0") + '"}\n'
            'wheels=[{hash="sha256:' + "a" * 64 + '"}]\n'
        )
        (self.root / "uv.lock").write_text(self.old_lock)
        workflow = self.root / ".github/workflows/validate.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "jobs:\n  validate:\n    uses: rakuzen25/jellyfin-plugin-toolkit/.github/workflows/validate.yml@"
            + "a" * 40
            + " # v0.2.0\n"
        )
        (self.root / "renovate.json").write_text(
            '{"extends":["config:recommended","github>rakuzen25/jellyfin-plugin-toolkit#v0.2.0","github>rakuzen25/jellyfin-plugin-toolkit:tooling#v0.2.0"],"automerge":false}\n'
        )
        self.assets = [
            {"name": "jellyfin_plugin_toolkit-0.3.0-py3-none-any.whl"},
            {"name": "jellyfin_plugin_toolkit-0.3.0.tar.gz"},
        ]

    def test_draft_or_incomplete_release_stops_before_resolution_and_writes(self):
        original = (self.root / "uv.lock").read_bytes()
        for release in [
            None,
            {"draft": True, "assets": self.assets},
            {"draft": False, "assets": []},
            {"draft": False, "assets": self.assets[:1]},
            {"draft": False, "assets": self.assets * 2},
        ]:
            with (
                self.subTest(release=release),
                patch(
                    "jellyfin_plugin_toolkit.release.service.release_for_tag",
                    return_value=release,
                ),
                patch.object(bindings.subprocess, "run") as uv,
                self.assertRaises(ValueError),
            ):
                bindings.plan(self.root)
            uv.assert_not_called()
            self.assertEqual(original, (self.root / "uv.lock").read_bytes())

    def test_wheel_only_proposal_derives_lock_workflows_and_preset_atomically(self):
        from jellyfin_plugin_toolkit.checks.pins import check_pins

        original = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

        def resolve(command, *, cwd, check):
            self.assertEqual(
                [
                    "uv",
                    "add",
                    "--no-sync",
                    "--no-build",
                    "jellyfin-plugin-toolkit @ " + self.url,
                ],
                command,
            )
            self.assertTrue(check)
            (cwd / "uv.lock").write_text(self.old_lock.replace("0.2.0", "0.3.0"))

        with (
            patch(
                "jellyfin_plugin_toolkit.release.service.release_for_tag",
                return_value={"draft": False, "assets": self.assets},
            ),
            patch(
                "jellyfin_plugin_toolkit.release.service.tag_commit",
                side_effect=lambda _, tag: ("b" if tag == "v0.3.0" else "a") * 40,
            ),
            patch(
                "jellyfin_plugin_toolkit.release.service.github_json",
                return_value=[
                    {"type": "file", "path": ".github/workflows/validate.yml"}
                ],
            ),
            patch.object(bindings.subprocess, "run", side_effect=resolve),
        ):
            changes = bindings.plan(self.root)
        self.assertEqual(original, {p: p.read_bytes() for p in original})
        self.assertEqual(
            {"uv.lock", "renovate.json", ".github/workflows/validate.yml"},
            {item.name for item in changes},
        )
        bindings.apply(self.root, changes)
        with patch(
            "jellyfin_plugin_toolkit.checks.pins.package_version", return_value="0.3.0"
        ):
            self.assertEqual("0.3.0", check_pins(self.root)["version"])
            self.assertIn(
                "b" * 40, (self.root / ".github/workflows/validate.yml").read_text()
            )
            configuration = self.root / "renovate.json"
            self.assertIn(":tooling#v0.3.0", configuration.read_text())
            configuration.write_text(
                configuration.read_text().replace(":tooling#v0.3.0", ":tooling#v0.2.0")
            )
            with self.assertRaisesRegex(ValueError, "preset differs"):
                check_pins(self.root)
            configuration.write_text(
                configuration.read_text().replace(":tooling#v0.2.0", ":tooling#v0.3.0")
            )
            configuration.write_text(
                configuration.read_text().replace("v0.3.0", "v0.4.0")
            )
            with self.assertRaisesRegex(ValueError, "preset differs"):
                check_pins(self.root)
