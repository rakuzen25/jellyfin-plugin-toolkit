import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scaffold_fixture import generate

from jellyfin_plugin_toolkit.context import Context


class StarterTests(unittest.TestCase):
    def test_backend_only_starter_has_no_empty_layers_or_frontend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            generate(None, root, "example", "Example", "example/plugin")
            import json

            renovate = json.loads((root / "renovate.json").read_text())
            self.assertNotIn("gitIgnoredAuthors", renovate)
            self.assertFalse((root / ".github/jpt-automation.json").exists())
            context = Context.load(root)
            self.assertEqual({"Host"}, set(context.config.backend.projects))
            self.assertFalse((root / "frontend").exists())
            self.assertFalse((root / "src/Core").exists())

    def test_administrator_starter_and_project_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            generate(
                None,
                root,
                "example",
                "Example",
                "example/plugin",
                True,
                "server/Entry.csproj",
            )
            import json

            renovate = json.loads((root / "renovate.json").read_text())
            self.assertNotIn("gitIgnoredAuthors", renovate)
            self.assertFalse((root / ".github/jpt-automation.json").exists())
            context = Context.load(root)
            self.assertTrue((root / "server/Entry.csproj").exists())
            self.assertTrue(context.config.frontend.enabled)
            self.assertTrue((root / "frontend/admin/config.ts").exists())
            self.assertFalse(context.config.distribution.web)

    def test_both_variants_generate_without_github_identity_requests(self) -> None:
        with (
            tempfile.TemporaryDirectory() as folder,
            patch(
                "jellyfin_plugin_toolkit.automation.resolve_bot",
                side_effect=AssertionError(
                    "Starter generation requested GitHub identity"
                ),
            ) as lookup,
        ):
            for admin in [False, True]:
                generate(
                    None,
                    Path(folder) / str(admin),
                    "example",
                    "Example",
                    "example/plugin",
                    admin,
                )
            lookup.assert_not_called()

    def test_existing_destination_and_escaping_project_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                generate(None, Path(directory), "example", "Example", "example/plugin")
            with self.assertRaises(ValueError):
                generate(
                    None,
                    Path(directory) / "plugin",
                    "example",
                    "Example",
                    "example/plugin",
                    False,
                    "../Outside.csproj",
                )

    def test_generated_repository_ignores_execution_reports(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            generate(None, root, "example", "Example", "example/plugin", True)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for name in [
                "tests/browser/results-run.fixture/.last-run.json",
                "tests/backend/Host/TestResults/results.trx",
            ]:
                result = subprocess.run(
                    ["git", "-C", str(root), "check-ignore", "--no-index", name],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, name)
            maintained = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "check-ignore",
                    "--no-index",
                    "tests/browser/admin.spec.cjs",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, maintained.returncode)

    def test_starter_inherits_quality_tools_and_development_adapter_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            generate(None, root, "example", "Example", "example/plugin", True)
            for filename in [
                "eslint.config.mjs",
                ".node-version",
                ".python-version",
                "global.json",
                "uv.lock",
                "pyproject.toml",
            ]:
                self.assertTrue((root / filename).is_file(), filename)
            self.assertFalse((root / "frontend/build.mjs").exists())
            self.assertNotIn("build", Context.load(root).config.frontend)
            self.assertNotIn("operations", Context.load(root).config)
            self.assertFalse((root / "tests/tooling/test_configuration.py").exists())
            self.assertIn(
                "configs/eslint.mjs", (root / "eslint.config.mjs").read_text()
            )
            self.assertIn("configs/ruff.toml", (root / "pyproject.toml").read_text())
            self.assertIn(
                "configs/prettier.mjs", (root / ".prettierrc.mjs").read_text()
            )
            self.assertIn(
                "configs/tsconfig.json", (root / "frontend/tsconfig.json").read_text()
            )
