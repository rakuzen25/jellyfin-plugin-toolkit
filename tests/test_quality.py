"""Native policy, standalone checks, and data-only upgrade contracts."""

import hashlib
import io
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from jellyfin_plugin_toolkit import bindings, quality, typing_policy
from jellyfin_plugin_toolkit.cli import main
from jellyfin_plugin_toolkit.resources_api import prepare


class QualityTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.project = self.root / "pyproject.toml"
        self.project.write_text(
            '[project]\nname="fixture"\nversion="0.0.0"\n'
            '[tool.ruff]\nextend=".build/jpt/configs/ruff.toml"\ntarget-version="py315"\n'
            '[tool.mypy]\nfiles=["sample.py"]\npython_version="3.15"\nstrict=true\n'
            '[tool.basedpyright]\nextends=".build/jpt/configs/basedpyright.toml"\ninclude=["sample.py"]\npythonVersion="3.15"\n'
            '[tool.jpt.quality]\nchecks=["python"]\n'
        )
        prepare(self.root)
        (self.root / "sample.py").write_text(
            "def identity(value: int) -> int:\n    return value\n"
        )

    def test_policy_sync_preserves_local_settings_and_is_idempotent(self) -> None:
        original = self.project.read_text().replace(
            "strict=true", "# local comment\nstrict=false"
        )
        changed = typing_policy.materialize(original)
        self.assertIn("# local comment", changed)
        self.assertIn('files=["sample.py"]', changed)
        self.assertEqual(True, tomllib.loads(changed)["tool"]["mypy"]["strict"])
        self.assertEqual(changed, typing_policy.materialize(changed))
        self.assertEqual(
            original,
            self.project.read_text().replace(
                "strict=true", "# local comment\nstrict=false"
            ),
        )

    def test_stale_policy_fails_without_rewriting(self) -> None:
        original = self.project.read_text().replace("strict=true", "strict=false")
        self.project.write_text(original)
        with self.assertRaisesRegex(ValueError, "policy is stale"):
            typing_policy.check_policy(self.root)
        self.assertEqual(original, self.project.read_text())

    def test_both_native_checkers_reject_an_annotation_error(self) -> None:
        (self.root / "sample.py").write_text('value: int = "wrong"\n')
        for tool in ("mypy", "basedpyright"):
            with self.subTest(tool=tool):
                result = subprocess.run(
                    [sys.executable, "-m", tool],
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertIn("sample.py", result.stdout)
                self.assertIn(
                    "assignment" if tool == "mypy" else "reportAssignmentType",
                    result.stdout,
                )

    def test_shared_ruff_uses_current_defaults(self) -> None:
        (self.root / "sample.py").write_text(
            "def mutable(values: list[int] = []) -> None:\n    values.append(1)\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "."],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("B006", result.stdout)

    def test_cli_checks_and_formats_without_plugin_metadata(self) -> None:
        original = self.project.read_bytes()
        with patch.object(quality, "main") as check:
            self.assertEqual(0, main(["--root", str(self.root), "check"]))
            check.assert_called_once_with(self.root, target=None)
            check.reset_mock()
            self.assertEqual(0, main(["--root", str(self.root), "format", "tooling"]))
            check.assert_called_once_with(self.root, fix=True, target="tooling")
        self.assertEqual(original, self.project.read_bytes())

    def test_shared_python_command_uses_native_config_and_current_interpreter(
        self,
    ) -> None:
        with patch.object(quality, "run") as run:
            quality.python(self.root)
        self.assertEqual(
            ["ruff", "mypy", "basedpyright"],
            [call.args[2] for call in run.call_args_list],
        )
        self.assertTrue(
            all(call.args[0] == sys.executable for call in run.call_args_list)
        )
        self.assertIn("--pythonpath", run.call_args_list[-1].args)

    def test_no_type_checks_are_invoked_by_format(self) -> None:
        with patch.object(quality, "run") as run:
            quality.main(self.root, fix=True)
        self.assertTrue(
            all(
                "mypy" not in call.args and "basedpyright" not in call.args
                for call in run.call_args_list
            )
        )

    def test_weaker_basedpyright_override_is_rejected(self) -> None:
        self.project.write_text(
            self.project.read_text().replace(
                "[tool.basedpyright]", '[tool.basedpyright]\ntypeCheckingMode="basic"'
            )
        )
        with self.assertRaisesRegex(ValueError, "weakens"):
            typing_policy.check_policy(self.root)

    def test_release_policy_is_read_as_verified_data(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as wheel:
            wheel.writestr(
                "jellyfin_plugin_toolkit/resources/configs/mypy.toml",
                "[tool.mypy]\nstrict=true\n",
            )
            wheel.writestr(
                "jellyfin_plugin_toolkit/__init__.py",
                'raise RuntimeError("must never execute")',
            )
        data = payload.getvalue()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(data)):
            policy = bindings.released_policy(
                "https://example.invalid/release.whl", hashlib.sha256(data).hexdigest()
            )
        self.assertEqual(True, tomllib.loads(policy.decode())["tool"]["mypy"]["strict"])
        with (
            patch("urllib.request.urlopen", return_value=io.BytesIO(data)),
            self.assertRaisesRegex(ValueError, "SHA256"),
        ):
            bindings.released_policy("https://example.invalid/release.whl", "0" * 64)

    def test_source_discovery_prunes_generated_trees_and_supports_root_selection(
        self,
    ) -> None:
        (self.root / "sample.py").unlink()
        ignored = self.root / ".build/generated.py"
        ignored.write_text("generated = True\n")
        self.assertFalse(quality.has_sources(self.root, ["."]))
        (self.root / "maintained.py").write_text("maintained = True\n")
        self.assertTrue(quality.has_sources(self.root, ["."]))

    def test_invalid_quality_capability_is_rejected(self) -> None:
        self.project.write_text(
            self.project.read_text().replace('checks=["python"]', 'checks=["unknown"]')
        )
        with self.assertRaises(ValueError):
            quality.selected(self.root)

    def test_automation_target_runs_only_applicable_validators(self) -> None:
        self.project.write_text(
            self.project.read_text().replace(
                'checks=["python"]',
                'checks=["python", "javascript", "workflows", "renovate"]\n'
                '[tool.jpt.quality.images]\nactionlint="fixture@sha256:'
                + "a" * 64
                + '"\nrenovate="fixture@sha256:'
                + "b" * 64
                + '"',
            )
        )
        with (
            patch.object(quality, "python") as python,
            patch.object(quality, "javascript") as javascript,
            patch.object(quality, "automation") as automation,
            patch.object(quality, "run"),
        ):
            quality.main(self.root, target="automation")
        python.assert_not_called()
        javascript.assert_not_called()
        self.assertEqual(
            ["workflows", "renovate"],
            [item.args[2] for item in automation.call_args_list],
        )
        with (
            patch.object(quality, "python"),
            patch.object(quality, "javascript"),
            patch.object(quality, "automation") as automation,
            patch.object(quality, "run"),
        ):
            quality.main(self.root, target="tooling")
        automation.assert_not_called()
