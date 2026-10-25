import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jellyfin_plugin_toolkit import bindings


def required_change(root: Path, name: str, after: bytes) -> bindings.Change:
    change = bindings.change_for(root, name, after)
    assert change is not None
    return change


class BindingTests(unittest.TestCase):
    def test_automation_target_preserves_local_configuration_and_is_idempotent(
        self,
    ) -> None:
        original = '# preserve\n[validation]\ncheck_targets = ["docs", "tooling"] # local\nunit_groups=["custom"]\n[operations.check-tooling]\nafter=[["architecture"]]\n'
        updated = bindings.automation_target(original)
        import tomllib

        self.assertEqual(
            ["docs", "tooling", "automation"],
            tomllib.loads(updated)["validation"]["check_targets"],
        )
        self.assertIn("# preserve", updated)
        self.assertIn('unit_groups=["custom"]', updated)
        self.assertIn('after=[["architecture"]]', updated)
        self.assertEqual(updated, bindings.automation_target(updated))

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "pyproject.toml").write_text("before")

    def test_transaction_rejects_concurrent_edits(self) -> None:
        change = required_change(self.root, "pyproject.toml", b"after")
        (self.root / "pyproject.toml").write_text("user edit")
        with self.assertRaises(ValueError):
            bindings.apply(self.root, [change])
        self.assertEqual("user edit", (self.root / "pyproject.toml").read_text())

    def test_failed_replacement_restores_originals(self) -> None:
        changes = [
            required_change(self.root, "pyproject.toml", b"after"),
            required_change(self.root, "uv.lock", b"new"),
        ]
        original = os.replace

        def fail(source: str | Path, target: str | Path) -> None:
            if str(source).endswith("/1"):
                raise OSError("injected")
            return original(source, target)

        with patch.object(os, "replace", side_effect=fail), self.assertRaises(OSError):
            bindings.apply(self.root, changes)
        self.assertEqual("before", (self.root / "pyproject.toml").read_text())
        self.assertFalse((self.root / "uv.lock").exists())

    def test_apply_is_idempotent_and_preserves_unrelated_files(self) -> None:
        (self.root / "unrelated").write_text("keep")
        bindings.apply(
            self.root, [required_change(self.root, "pyproject.toml", b"after")]
        )
        self.assertIsNone(bindings.change_for(self.root, "pyproject.toml", b"after"))
        self.assertEqual("keep", (self.root / "unrelated").read_text())

    def test_quoted_workflow_reference_preserves_formatting(self) -> None:
        old: dict[str, Any] = {
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
        files: dict[str, Any] = {
            ".github/workflows/validate.yml": b"workflow",
            "toolkit-release.json": json.dumps(
                {"version": "0.3.0", "revision": "b" * 40}
            ).encode(),
        }
        updated = bindings.rewrite_workflow(text, old, new, files)
        self.assertIn("b" * 40, updated)
        self.assertTrue(updated.endswith("\r\n"))

    def test_named_preset_drift_stops_rewrite_and_unrelated_presets_are_preserved(
        self,
    ) -> None:
        old: dict[str, Any] = {
            "repository": "rakuzen25/jellyfin-plugin-toolkit",
            "version": "v0.2.0",
        }
        new = dict(old, version="v0.3.0")
        config = '{"extends": ["github>example/policy#v9.0.0", "github>rakuzen25/jellyfin-plugin-toolkit:tooling#v0.1.0"]}'
        with self.assertRaisesRegex(ValueError, "preset drift"):
            bindings.rewrite_preset(config, old, new)
        config = config.replace(":tooling#v0.1.0", ":tooling#v0.2.0")
        updated = bindings.rewrite_preset(config, old, new)
        self.assertIn("github>example/policy#v9.0.0", updated)
        self.assertIn(":tooling#v0.3.0", updated)

    def test_source_update_requires_canonical_github_wheel(self) -> None:
        (self.root / "pyproject.toml").write_text(
            '[tool.uv.sources]\njellyfin-plugin-toolkit = {url="https://attacker.invalid/tool.whl"}'
        )
        with self.assertRaises(ValueError):
            bindings.target(self.root)


class ReleaseBindingPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.url = "https://github.com/rakuzen25/jellyfin-plugin-toolkit/releases/download/v0.3.0/jellyfin_plugin_toolkit-0.3.0-py3-none-any.whl"
        (self.root / "pyproject.toml").write_text(
            '[project]\nname="fixture"\ndependencies=["jellyfin-plugin-toolkit"]\n'
            '[tool.mypy]\nfiles=["src"]\nstrict=false\n'
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

    def test_draft_or_incomplete_release_stops_before_resolution_and_writes(
        self,
    ) -> None:
        original = (self.root / "uv.lock").read_bytes()
        cases_release: list[dict[str, Any] | None] = [
            None,
            {"draft": True, "assets": self.assets},
            {"draft": False, "assets": []},
            {"draft": False, "assets": self.assets[:1]},
            {"draft": False, "assets": self.assets * 2},
        ]
        for release in cases_release:
            with (
                self.subTest(release=release),
                patch(
                    "jellyfin_plugin_toolkit.release.service.release_for_tag",
                    return_value=release,
                ),
                patch.object(subprocess, "run") as uv,
                self.assertRaises(ValueError),
            ):
                bindings.plan(self.root)
            uv.assert_not_called()
            self.assertEqual(original, (self.root / "uv.lock").read_bytes())

    def test_wheel_only_proposal_derives_lock_workflows_and_preset_atomically(
        self,
    ) -> None:
        from jellyfin_plugin_toolkit.checks.pins import check_pins

        original = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

        def resolve(command: list[str], *, cwd: Path, check: bool) -> None:
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

        def mock_tag_commit(_: object, tag: str) -> Any:
            return ("b" if tag == "v0.3.0" else "a") * 40

        with (
            patch(
                "jellyfin_plugin_toolkit.release.service.release_for_tag",
                return_value={"draft": False, "assets": self.assets},
            ),
            patch(
                "jellyfin_plugin_toolkit.release.service.tag_commit",
                side_effect=mock_tag_commit,
            ),
            patch(
                "jellyfin_plugin_toolkit.release.service.github_json",
                return_value=[
                    {"type": "file", "path": ".github/workflows/validate.yml"}
                ],
            ),
            patch.object(subprocess, "run", side_effect=resolve),
            patch.object(
                bindings, "released_policy", return_value=b"[tool.mypy]\nstrict=true\n"
            ),
        ):
            changes = bindings.plan(self.root)
        self.assertEqual(original, {p: p.read_bytes() for p in original})
        self.assertEqual(
            {
                "pyproject.toml",
                "uv.lock",
                "renovate.json",
                ".github/workflows/validate.yml",
            },
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

    def test_development_wheel_is_not_promoted_to_runtime(self) -> None:
        project = self.root / "pyproject.toml"
        text = project.read_text().replace(
            'dependencies=["jellyfin-plugin-toolkit"]',
            'dependencies=[]\n[dependency-groups]\ndev=["jellyfin-plugin-toolkit"]',
        )
        project.write_text(text)

        def resolve(command: list[str], *, cwd: Path, check: bool) -> None:
            self.assertIn("--dev", command)
            self.assertTrue(check)
            (cwd / "uv.lock").write_text(self.old_lock.replace("0.2.0", "0.3.0"))

        def tag(_: str, version: str) -> str:
            return ("b" if version == "v0.3.0" else "a") * 40

        with (
            patch(
                "jellyfin_plugin_toolkit.release.service.release_for_tag",
                return_value={"draft": False, "assets": self.assets},
            ),
            patch(
                "jellyfin_plugin_toolkit.release.service.tag_commit", side_effect=tag
            ),
            patch(
                "jellyfin_plugin_toolkit.release.service.github_json",
                return_value=[
                    {"type": "file", "path": ".github/workflows/validate.yml"}
                ],
            ),
            patch.object(
                bindings, "released_policy", return_value=b"[tool.mypy]\nstrict=true\n"
            ),
            patch.object(subprocess, "run", side_effect=resolve),
        ):
            changes = bindings.plan(self.root)
        bindings.apply(self.root, changes)
        import tomllib

        metadata = tomllib.loads(project.read_text())
        self.assertEqual([], metadata["project"]["dependencies"])
        self.assertEqual(
            ["jellyfin-plugin-toolkit"], metadata["dependency-groups"]["dev"]
        )
        self.assertTrue(metadata["tool"]["mypy"]["strict"])
