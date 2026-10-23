"""Native uv package, cold environment, lock and resource integration contracts."""

import hashlib
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scaffold_fixture import ROOT, candidate, generate

from jellyfin_plugin_toolkit.provenance import TOOLKIT_DIST, distributions, record
from jellyfin_plugin_toolkit.resources_api import prepare


class NativeUvTests(unittest.TestCase):
    def test_wheel_contains_all_resources_without_vendored_dependencies(self):
        with zipfile.ZipFile(candidate()) as wheel:
            names = set(wheel.namelist())
        for name in (
            "configs/plugin.schema.json",
            "node/build.mjs",
            "ui/controls.ts",
            "msbuild/Plugin.props",
            "templates/defaults.json",
            "actions/setup/action.yml",
        ):
            self.assertIn("jellyfin_plugin_toolkit/resources/" + name, names)
        self.assertIn("jellyfin_plugin_toolkit/py.typed", names)
        self.assertIn("jellyfin_plugin_toolkit/automation_setup.py", names)
        self.assertFalse(
            any("_vendor/" in name or name.endswith("bootstrap.py") for name in names)
        )

    def test_native_build_is_repeatable(self):
        first = candidate().read_bytes()
        source = candidate().with_name(
            candidate().name.replace("-py3-none-any.whl", ".tar.gz")
        )
        first_source = source.read_bytes()
        with tempfile.TemporaryDirectory(dir=ROOT / ".build") as folder:
            subprocess.run(
                ["uv", "build", "--out-dir", folder],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            self.assertEqual(first_source, (Path(folder) / source.name).read_bytes())
            self.assertEqual(
                hashlib.sha256(first).digest(),
                hashlib.sha256((Path(folder) / candidate().name).read_bytes()).digest(),
            )

    def test_cold_project_installs_wheel_and_runs_without_pythonpath(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".build") as folder:
            root = Path(folder) / "consumer"
            generate(None, root, "cold", "Cold", "example/cold", True)
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            subprocess.run(
                ["uv", "sync", "--locked"],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    "-I",
                    "-c",
                    "from jellyfin_plugin_toolkit.scaffold import generate;import sys;print(sys.executable)",
                ],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn(str(root / ".venv"), result.stdout)
            subprocess.run(
                ["uv", "run", "--no-sync", "jpt", "prepare"],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
            )
            self.assertTrue((root / ".build/jpt/node/build.mjs").exists())
            self.assertFalse(
                (root / ".build/jpt/jellyfin_plugin_toolkit/__init__.py").exists()
            )
            selected = record(root)
            self.assertEqual(
                hashlib.sha256(candidate().read_bytes()).hexdigest(), selected["sha256"]
            )
            with self.assertRaisesRegex(ValueError, "released GitHub wheel"):
                record(root, release=True)
            retained = root / ".build/toolkit-dist" / candidate().name
            original = retained.read_bytes()
            with zipfile.ZipFile(retained, "a") as changed:
                changed.writestr("tamper.txt", "Different valid wheel bytes")
            with self.assertRaisesRegex(ValueError, "locked SHA256"):
                record(root)
            rejected = subprocess.run(
                [
                    "uv",
                    "sync",
                    "--locked",
                    "--reinstall-package",
                    "jellyfin-plugin-toolkit",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn(b"Hash mismatch", rejected.stderr)
            retained.write_bytes(original)
            project = root / "pyproject.toml"
            project.write_text(
                project.read_text().replace(
                    "dependencies = [", 'dependencies = ["packaging>=25",', 1
                )
            )
            before = (root / "uv.lock").read_bytes()
            check = subprocess.run(
                ["uv", "sync", "--locked"],
                cwd=root,
                env=environment,
                capture_output=True,
            )
            self.assertNotEqual(0, check.returncode)
            self.assertEqual(before, (root / "uv.lock").read_bytes())

    def test_resource_projection_repairs_removed_resources_and_rejects_redirection(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            exported = prepare(root)
            (exported / "node/build.mjs").unlink()
            prepare(root)
            self.assertTrue((exported / "node/build.mjs").is_file())
            import shutil

            shutil.rmtree(exported)
            exported.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                prepare(root)

    def test_workflow_resource_references_exist_in_installed_wheel(self):
        import re

        with zipfile.ZipFile(candidate()) as wheel:
            names = set(wheel.namelist())
        found = set()
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            for reference in re.findall(
                r"uses: \./\.build/jpt/([^\s]+)", workflow.read_text()
            ):
                found.add(reference)
                self.assertIn(
                    "jellyfin_plugin_toolkit/resources/" + reference + "/action.yml",
                    names,
                )
        self.assertTrue(found)

    def test_console_new_succeeds_outside_consumer_checkout(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".build") as folder:
            root = Path(folder)
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "--project",
                    str(ROOT),
                    "--no-sync",
                    "jpt",
                    "new",
                    str(root / "plugin"),
                    "--id",
                    "console",
                    "--name",
                    "Console",
                    "--repository",
                    "fixture/console",
                    "--toolkit-wheel",
                    str(candidate()),
                    "--toolkit-revision",
                    "a" * 40,
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((root / "plugin/uv.lock").is_file())
            self.assertFalse((root / ".build").exists())

    def test_sdist_retains_native_lock_pin_and_resource_source(self):
        import tarfile

        source = candidate().with_name(
            candidate().name.replace("-py3-none-any.whl", ".tar.gz")
        )
        with tarfile.open(source) as package:
            names = {"/".join(name.split("/")[1:]) for name in package.getnames()}
        for name in [
            "uv.lock",
            ".python-version",
            "src/jellyfin_plugin_toolkit/resources/actions/setup/action.yml",
            "src/jellyfin_plugin_toolkit/cli.py",
        ]:
            self.assertIn(name, names)

    def test_source_bundle_retains_candidate_and_installs_after_extraction(self):
        from jellyfin_plugin_toolkit.acceptance import inventory
        from jellyfin_plugin_toolkit.build.clean_package import clean_package
        from jellyfin_plugin_toolkit.build.package_artifacts import assemble
        from jellyfin_plugin_toolkit.context import Context

        with tempfile.TemporaryDirectory(dir=ROOT / ".build") as folder:
            root = Path(folder) / "consumer"
            generate(None, root, "bundle", "Bundle", "example/bundle")
            ctx = Context.load(root)
            tools = distributions(root)
            original = {path.name: path.read_bytes() for path in tools}
            lock = (root / "uv.lock").read_bytes()
            self.assertEqual(
                (TOOLKIT_DIST / candidate().name).as_posix(), record(root)["source"]
            )
            self.assertNotIn("build-support", (root / ".gitignore").read_text())
            for _ in range(2):
                prepare(root)
                self.assertEqual(original, {p.name: p.read_bytes() for p in tools})
            (root / ".build/unrelated.txt").write_text("not source")
            run = subprocess.run

            def package_in_clean_checkout(command, *, cwd, **kwargs):
                if command == ["uv", "sync", "--locked"]:
                    return run(command, cwd=cwd, capture_output=True, **kwargs)
                self.assertEqual(
                    ["uv", "run", "--no-sync", "jpt", "internal", "package"], command
                )
                clean = Context.load(cwd)
                self.assertEqual(lock, (cwd / "uv.lock").read_bytes())
                self.assertEqual(
                    original, {p.name: p.read_bytes() for p in distributions(cwd)}
                )
                self.assertFalse((cwd / ".build/unrelated.txt").exists())
                staging = cwd / ".build/staging"
                plugin = staging / "plugin"
                plugin.mkdir(parents=True)
                (plugin / (clean.identity.assembly + ".dll")).write_bytes(
                    b"fixture binary"
                )
                with patch(
                    "jellyfin_plugin_toolkit.dev.references.inventory", return_value={}
                ):
                    assemble(clean, staging)
                return subprocess.CompletedProcess(command, 0)

            with patch(
                "jellyfin_plugin_toolkit.build.clean_package.subprocess.run",
                side_effect=package_in_clean_checkout,
            ):
                clean_package(ctx)
            inventory(ctx)
            source = next((root / "dist").glob("*-source-*.zip"))
            extracted = Path(folder) / "extracted"
            with zipfile.ZipFile(source) as bundle:
                retained = {
                    name for name in bundle.namelist() if name.startswith(".build/")
                }
                self.assertEqual(
                    {(TOOLKIT_DIST / name).as_posix() for name in original}, retained
                )
                bundle.extractall(extracted)
            self.assertEqual(lock, (extracted / "uv.lock").read_bytes())
            self.assertEqual(
                original, {p.name: p.read_bytes() for p in distributions(extracted)}
            )
            subprocess.run(
                ["uv", "sync", "--locked"],
                cwd=extracted,
                check=True,
                capture_output=True,
            )
            self.assertEqual(lock, (extracted / "uv.lock").read_bytes())

    def test_candidate_provenance_rejects_symlinked_build_parent_before_reading(self):
        import shutil

        with tempfile.TemporaryDirectory(dir=ROOT / ".build") as folder:
            root = Path(folder) / "consumer"
            generate(None, root, "redirect", "Redirect", "example/redirect")
            outside = Path(folder) / "outside"
            shutil.move(root / ".build", outside)
            (root / ".build").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "directories"):
                record(root)
