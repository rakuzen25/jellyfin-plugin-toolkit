"""Release update rules follow maintained internal references, including new projects."""

import copy
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from consumer_fixture import config
from scaffold_fixture import generate

from jellyfin_plugin_toolkit.cli import main
from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.release import configuration
from jellyfin_plugin_toolkit.release.service import check_identity, prepare
from jellyfin_plugin_toolkit.scaffold import toml


def fixture(root: Path) -> Context:
    selected = copy.deepcopy(config)
    selected["backend"]["project_assemblies"] = ["Example.Core", "Example.Application"]
    selected["source"] = {"roots": ["src", "tests", "custom"]}
    (root / "plugin.toml").write_text(toml(selected))
    (root / "version.txt").write_text("0.1.0\n")
    (root / "CHANGELOG.md").write_text(
        "## 0.1.1 (2026-10-20)\n\nNext release.\n\n"
        "## 0.1.0 (2026-10-19)\n\nInitial release.\n"
    )
    data: dict[str, Any] = {
        "release-type": "simple",
        "draft": True,
        "packages": {".": {"package-name": "example", "extra-files": ["other.txt"]}},
    }
    (root / configuration.FILENAME).write_text(json.dumps(data, indent=4) + "\n")
    for name in [
        "src/Host",
        "src/Application",
        "tests/backend/Application",
        "custom/Host",
    ]:
        path = root / name / "packages.lock.json"
        path.parent.mkdir(parents=True)
        projects: dict[str, Any] = {
            "example.application": {
                "type": "Project",
                "dependencies": {
                    "Example.Core": "[0.1.0, )",
                    "External.Package": "[4.0.0, )",
                },
            },
            "example.host": {
                "type": "Project",
                "dependencies": {
                    "Example.Application": "[0.1.0, )",
                    selected["plugin"]["assembly"]: "[0.1.0, )",
                },
            },
            "external.package": {
                "type": "Transitive",
                "resolved": "4.0.0",
                "dependencies": {
                    "Example.Core": "[4.0.0, )",
                },
            },
        }
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "dependencies": {
                        "net10.0": projects,
                        "net11.0": projects,
                    },
                },
                indent=2,
            )
            + "\n"
        )
    return Context.load(root)


class ReleaseConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ctx = fixture(self.root)

    def apply(self) -> None:
        configuration.configure(self.root, apply=True)

    def test_preview_check_apply_and_idempotency_preserve_other_settings(self) -> None:
        original = (self.root / configuration.FILENAME).read_bytes()
        with redirect_stdout(io.StringIO()) as output:
            main(["--root", str(self.root), "release", "config"])
        self.assertIn("+", output.getvalue())
        self.assertEqual(original, (self.root / configuration.FILENAME).read_bytes())
        self.assertEqual(
            1, main(["--root", str(self.root), "release", "config", "--check"])
        )
        self.apply()
        main(["--root", str(self.root), "release", "config", "--check"])
        data = json.loads((self.root / configuration.FILENAME).read_text())
        self.assertTrue(data["draft"])
        self.assertEqual("other.txt", data["packages"]["."]["extra-files"][0])
        rules = data["packages"]["."]["extra-files"][1:]
        self.assertEqual(4, len(rules))
        self.assertEqual(
            sorted(rule["path"] for rule in rules), [rule["path"] for rule in rules]
        )
        before = (self.root / configuration.FILENAME).read_bytes()
        self.apply()
        self.assertEqual(before, (self.root / configuration.FILENAME).read_bytes())
        self.assertEqual(
            2,
            main(["--root", str(self.root), "release", "config", "--check", "--apply"]),
        )

    def test_new_removed_locks_and_changed_assemblies_require_regeneration(
        self,
    ) -> None:
        self.apply()
        path = self.root / "src/Added/packages.lock.json"
        path.parent.mkdir()
        path.write_bytes((self.root / "src/Host/packages.lock.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "incomplete or stale"):
            configuration.check(self.root)
        self.apply()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "incomplete or stale"):
            configuration.check(self.root)
        self.apply()
        selected = self.ctx.config.data()
        selected["backend"]["project_assemblies"].append("Example.Infrastructure")
        (self.root / "plugin.toml").write_text(toml(selected))
        with self.assertRaisesRegex(ValueError, "incomplete or stale"):
            configuration.check(self.root)
        self.apply()
        configuration.check(self.root)

    def test_locks_without_internal_dependencies_need_no_rule(self) -> None:
        path = self.root / "src/Empty/packages.lock.json"
        path.parent.mkdir()
        path.write_text(
            '{"dependencies":{"net10.0":{"external":{"type":"Direct","resolved":"1.0.0"}}}}'
        )
        self.apply()
        self.assertNotIn("src/Empty", (self.root / configuration.FILENAME).read_text())

    def test_config_commands_never_modify_lockfiles_or_version(self) -> None:
        paths = [*self.root.rglob("packages.lock.json"), self.root / "version.txt"]
        before = {path: path.read_bytes() for path in paths}
        self.apply()
        configuration.check(self.root)
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_empty_lock_removes_its_generated_rule(self) -> None:
        self.apply()
        (self.root / "src/Host/packages.lock.json").write_text(
            '{"dependencies": {"net10.0": {}}}'
        )
        self.apply()
        self.assertNotIn(
            '"path": "src/Host/packages.lock.json"',
            (self.root / configuration.FILENAME).read_text(),
        )

    def test_custom_overlaps_are_rejected_without_partial_writes(self) -> None:
        for rule in [
            "src/Host/packages.lock.json",
            "./src/Host/packages.lock.json",
            "tests/../src/Host/packages.lock.json",
            {
                "type": "json",
                "path": "src/Host/packages.lock.json",
                "jsonpath": "$..dependencies.*",
            },
        ]:
            with self.subTest(rule=rule):
                data = json.loads((self.root / configuration.FILENAME).read_text())
                data["packages"]["."]["extra-files"] = [rule]
                path = self.root / configuration.FILENAME
                path.write_text(json.dumps(data))
                before = path.read_bytes()
                with self.assertRaisesRegex(
                    ValueError, "src/Host/packages.lock.json; remove"
                ):
                    self.apply()
                self.assertEqual(before, path.read_bytes())

    def test_malformed_and_unsupported_configurations_have_actionable_errors(
        self,
    ) -> None:
        for data, message in [
            ([], "single '.' entry"),
            ({"packages": {"a": {}, "b": {}}}, "single '.' package"),
            ({"release-type": "node", "packages": {".": {}}}, "release-type 'simple'"),
            (
                {"release-type": "simple", "packages": {".": {"extra-files": {}}}},
                "must be an array",
            ),
            (
                {"release-type": "simple", "packages": {".": {"extra-files": [None]}}},
                "require a path",
            ),
        ]:
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, message):
                configuration.generated_config(self.ctx, data)
        (self.root / configuration.FILENAME).write_text("{")
        with self.assertRaisesRegex(
            ValueError, "Cannot read release-please-config.json"
        ):
            configuration.check(self.root)
        (self.root / "src/Host/packages.lock.json").write_text('{"dependencies": []}')
        with self.assertRaisesRegex(ValueError, "Invalid NuGet lockfile src/Host"):
            configuration.internal_references(self.ctx)

    def test_prepare_checks_rules_before_writing_environment(self) -> None:
        output = self.root / "environment"
        with self.assertRaisesRegex(ValueError, "incomplete or stale"):
            prepare(self.root, "example/plugin", "", output)
        self.assertFalse(output.exists())
        self.apply()
        prepare(self.root, "example/plugin", "", output)
        self.assertIn("v0.1.0", output.read_text())

    def test_version_failure_reports_exact_internal_dependency(self) -> None:
        (self.root / "version.txt").write_text("0.1.1\n")
        with self.assertRaisesRegex(
            ValueError, r"packages.lock.json: net10.0/.*Example.Core.*0.1.0.*0.1.1"
        ):
            check_identity(self.root)

    def test_both_starters_pass_without_installing_dependencies(self) -> None:
        for admin in [False, True]:
            with self.subTest(admin=admin):
                root = self.root / ("admin" if admin else "backend")
                generate(None, root, "example", "Example", "example/plugin", admin)
                configuration.check(root)

    def test_workflows_enforce_rules_before_release_please_and_ci_selection(
        self,
    ) -> None:
        toolkit = Path(__file__).resolve().parents[1]
        workflow = (toolkit / ".github/workflows/prepare-release.yml").read_text()
        self.assertLess(
            workflow.index("jpt release config --check"),
            workflow.index("uses: googleapis/release-please-action"),
        )
        action_match = re.search(r"release-please-action@([a-f0-9]{40})", workflow)
        assert action_match is not None
        action = action_match[1]
        ci = (toolkit / ".github/workflows/ci.yml").read_text()
        self.assertIn("ref: " + action, ci)
        validation = (toolkit / ".github/workflows/validate.yml").read_text()
        self.assertLess(
            validation.index("jpt release prepare"),
            validation.index("jpt internal ci plan"),
        )
