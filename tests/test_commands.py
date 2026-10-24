"""Public command defaults, argument forwarding and failure propagation."""

import io
import json
import subprocess
from contextlib import redirect_stdout
from typing import cast
from unittest.mock import call, patch

from consumer_fixture import ConsumerTestCase

from jellyfin_plugin_toolkit import tasks
from jellyfin_plugin_toolkit.cli import main
from jellyfin_plugin_toolkit.configuration import Configuration, Operation
from jellyfin_plugin_toolkit.context import Context


class CommandTests(ConsumerTestCase):
    def test_advisories_use_shared_evaluator(self) -> None:
        with patch("jellyfin_plugin_toolkit.advisories.audit") as audit:
            tasks.test_default(self.ctx, "advisories", [])
        audit.assert_called_once_with(self.ctx)

    def test_builds_leave_advisory_decisions_to_the_audit_gate(self) -> None:
        import xml.etree.ElementTree as ET

        from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT

        properties = ET.parse(TOOLKIT_ROOT / "msbuild/Plugin.props").getroot()
        self.assertEqual(
            properties.findtext("PropertyGroup/WarningsNotAsErrors"),
            "$(WarningsNotAsErrors);NU1901;NU1902;NU1903;NU1904",
        )
        self.assertEqual(properties.findtext(".//TreatWarningsAsErrors"), "true")

    def test_doctor_stdout_is_json(self) -> None:
        with (
            patch.object(tasks, "verify_tools"),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(0, main(["doctor"]))
        self.assertEqual(self.ctx.identity.id, json.loads(output.getvalue())["plugin"])

    def test_validate_runs_every_local_stage_in_order(self) -> None:
        events: list[object] = []
        with (
            patch.object(
                tasks,
                "check",
                side_effect=lambda _, target: events.append(("check", target)),
            ),
            patch.object(
                tasks,
                "test",
                side_effect=lambda _, target, extra: events.append(("test", target)),
            ),
            patch(
                "jellyfin_plugin_toolkit.build.clean_package.clean_package",
                side_effect=lambda _: events.append(("package", None)),
            ),
        ):
            self.assertEqual(0, main(["validate"]))
        self.assertEqual(
            [("check", target) for target in self.ctx.config.validation.check_targets]
            + [("test", target) for target in self.ctx.config.validation.unit_groups]
            + [("package", None)]
            + [
                ("test", target)
                for target in self.ctx.config.validation.acceptance_groups
            ]
            + [("test", "advisories")],
            events,
        )

    def test_default_tests_do_not_run_acceptance(self) -> None:
        with patch.object(tasks, "test") as test:
            main(["test"])
        self.assertEqual(
            [call(self.ctx, "backend", []), call(self.ctx, "tooling", [])],
            test.call_args_list,
        )

    def test_browser_arguments_are_forwarded_literally(self) -> None:
        with patch.object(tasks, "test") as test:
            main(
                [
                    "test",
                    "browser",
                    "--",
                    "--project",
                    "workflow-chromium",
                    "--grep",
                    "literal $(value)",
                ]
            )
        test.assert_called_once_with(
            self.ctx,
            "browser",
            ["--project", "workflow-chromium", "--grep", "literal $(value)"],
        )

    def test_check_stops_after_first_failure(self) -> None:
        with (
            patch.object(
                tasks, "check", side_effect=subprocess.CalledProcessError(1, "check")
            ) as check,
        ):
            self.assertEqual(1, main(["check"]))
        self.assertEqual(1, check.call_count)

    def test_docs_checks_format_before_links_without_installing(self) -> None:
        with (
            patch("jellyfin_plugin_toolkit.checks.formatting.run") as run,
            patch("jellyfin_plugin_toolkit.checks.docs.main") as links,
        ):
            tasks.check(self.ctx, "docs")
        links.assert_called_once_with(self.ctx)
        self.assertIn("--check", run.call_args.args)
        self.assertNotIn("install", run.call_args.args)
        with (
            patch(
                "jellyfin_plugin_toolkit.checks.formatting.run",
                side_effect=subprocess.CalledProcessError(1, "prettier"),
            ),
            patch("jellyfin_plugin_toolkit.checks.docs.main") as links,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            tasks.check(self.ctx, "docs")
        links.assert_not_called()

    def test_operations_use_argv_and_preserve_globs(self) -> None:
        context = Context.load(self.root)
        selected = context.config.data()
        selected["operations"] = {
            "test-example": {
                "commands": [["{python}", "fixture.py", "**/*.{ts,mjs}", "$(literal)"]]
            }
        }
        with (
            patch.object(tasks, "run") as run,
        ):
            self.assertTrue(
                tasks.operation(
                    Context(self.root, Configuration.model_validate(selected)),
                    "test-example",
                    ["--workers=2"],
                )
            )
        self.assertEqual(
            ("fixture.py", "**/*.{ts,mjs}", "$(literal)", "--workers=2"),
            run.call_args.args[1:],
        )
        self.assertEqual(self.root, run.call_args.kwargs["cwd"])

    def test_bootstrap_leaves_python_environment_to_uv(self) -> None:
        with (
            patch.object(tasks, "verify_tools"),
            patch.object(Context, "generated"),
            patch.object(tasks, "prepare"),
            patch("pathlib.Path.exists", return_value=True),
            patch.object(tasks, "run") as run,
        ):
            tasks.bootstrap(self.ctx)
        self.assertTrue(
            all(
                "pip" not in call.args and "venv" not in call.args
                for call in run.call_args_list
            )
        )
        self.assertEqual("pnpm", run.call_args.args[0])

    def test_backend_tests_prepare_missing_embedded_resources_without_installing(
        self,
    ) -> None:
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = SimpleNamespace(
                root=root,
                config=SimpleNamespace(
                    frontend=SimpleNamespace(
                        enabled=True, required_assets=["config.html"]
                    ),
                    backend=SimpleNamespace(tests=[]),
                ),
                generated=Mock(),
            )
            with (
                patch.object(tasks, "frontend_build") as build,
                patch.object(tasks, "run") as run,
            ):
                tasks.backend_tests(cast(Context, context), [])
                build.assert_called_once_with(context)
                run.assert_not_called()
                (root / "dist/web").mkdir(parents=True)
                (root / "dist/web/config.html").write_text("verified prebuilt resource")
                build.reset_mock()
                tasks.backend_tests(cast(Context, context), [])
                build.assert_not_called()

    def test_common_tooling_checks_never_restore_dependencies(self) -> None:
        from jellyfin_plugin_toolkit.checks import tooling

        with (
            patch.object(tooling, "check_pins") as pins,
            patch.object(tooling, "check_workflows") as workflows,
            patch(
                "jellyfin_plugin_toolkit.checks.formatting.format_target"
            ) as formatting,
            patch.object(tooling, "run") as run,
        ):
            tasks.check(self.ctx, "tooling")
        pins.assert_called_once_with(self.root)
        workflows.assert_called_once_with(self.root)
        formatting.assert_called_once_with(self.ctx, "tooling", False)
        self.assertTrue(
            any("eslint" in invocation.args for invocation in run.call_args_list)
        )
        self.assertTrue(
            all("install" not in invocation.args for invocation in run.call_args_list)
        )

    def test_tooling_check_preserves_consumer_formatting_override(self) -> None:
        self.ctx.config.operations = {
            key: Operation.model_validate(value)
            for key, value in (
                {"format-check-tooling": {"commands": [["formatter", "--verify"]]}}
            ).items()
        }
        with (
            patch.object(tasks, "run") as run,
            patch(
                "jellyfin_plugin_toolkit.checks.formatting.format_target"
            ) as default_format,
            patch("jellyfin_plugin_toolkit.checks.tooling.main") as lint,
        ):
            tasks.check(self.ctx, "tooling")
        self.assertEqual(("formatter", "--verify"), run.call_args.args)
        self.assertEqual(self.root, run.call_args.kwargs["cwd"])
        default_format.assert_not_called()
        lint.assert_called_once_with(self.ctx)


class AfterOperationTests(ConsumerTestCase):
    def test_standard_check_finishes_before_after_commands(self) -> None:
        self.ctx.config.operations = {
            key: Operation.model_validate(value)
            for key, value in (
                {"check-docs": {"after": [["extra", "{root}"]], "cwd": "docs"}}
            ).items()
        }
        (self.root / "docs").mkdir()
        events: list[object] = []
        with (
            patch.object(
                tasks, "check_default", side_effect=lambda *_: events.append("default")
            ),
            patch.object(
                tasks,
                "run",
                side_effect=lambda *args, **kw: events.append((args, kw["cwd"])),
            ),
        ):
            tasks.check(self.ctx, "docs")
        self.assertEqual(
            ["default", (("extra", str(self.root)), self.root / "docs")], events
        )

    def test_standard_failure_skips_after_commands(self) -> None:
        self.ctx.config.operations = {
            key: Operation.model_validate(value)
            for key, value in ({"check-docs": {"after": [["extra"]]}}).items()
        }
        with (
            patch.object(
                tasks,
                "check_default",
                side_effect=subprocess.CalledProcessError(1, "standard"),
            ),
            patch.object(tasks, "run") as run,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            tasks.check(self.ctx, "docs")
        run.assert_not_called()

    def test_after_failure_stops_remaining_commands(self) -> None:
        self.ctx.config.operations = {
            key: Operation.model_validate(value)
            for key, value in (
                {"check-docs": {"after": [["first"], ["second"]]}}
            ).items()
        }
        with (
            patch.object(tasks, "check_default") as default,
            patch.object(
                tasks, "run", side_effect=subprocess.CalledProcessError(1, "first")
            ) as run,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            tasks.check(self.ctx, "docs")
        default.assert_called_once_with(self.ctx, "docs")
        self.assertEqual(1, run.call_count)

    def test_test_arguments_reach_default_but_not_after(self) -> None:
        self.ctx.config.operations = {
            key: Operation.model_validate(value)
            for key, value in ({"test-backend": {"after": [["extra"]]}}).items()
        }
        with (
            patch.object(tasks, "test_default") as default,
            patch.object(tasks, "run") as run,
        ):
            tasks.test(self.ctx, "backend", ["--filter", "literal $(name)"])
        default.assert_called_once_with(
            self.ctx, "backend", ["--filter", "literal $(name)"]
        )
        self.assertEqual(("extra",), run.call_args.args)

    def test_replacement_still_receives_arguments_without_running_default(self) -> None:
        self.ctx.config.operations = {
            key: Operation.model_validate(value)
            for key, value in ({"test-example": {"commands": [["custom"]]}}).items()
        }
        with (
            patch.object(tasks, "test_default") as default,
            patch.object(tasks, "run") as run,
        ):
            tasks.test(self.ctx, "example", ["--literal"])
        default.assert_not_called()
        self.assertEqual(("custom", "--literal"), run.call_args.args)

    def test_format_after_uses_standard_formatting(self) -> None:
        self.ctx.config.operations = {
            key: Operation.model_validate(value)
            for key, value in ({"format-docs": {"after": [["extra"]]}}).items()
        }
        with (
            patch("jellyfin_plugin_toolkit.checks.formatting.format_target") as default,
            patch.object(tasks, "run") as run,
        ):
            tasks.format_target(self.ctx, "docs", True)
        default.assert_called_once_with(self.ctx, "docs", True)
        self.assertEqual(("extra",), run.call_args.args)

    def test_invalid_hooks_are_rejected_by_context_loading(self) -> None:
        from jellyfin_plugin_toolkit.scaffold import toml

        for spec in [
            {"commands": [["replace"]], "after": [["extra"]]},
            {"after": []},
            {"after": ["shell command"]},
            {"after": [[]]},
            {},
        ]:
            with self.subTest(spec=spec):
                selected = self.ctx.config.data()
                selected["operations"] = {"check-tooling": spec}
                (self.root / "plugin.toml").write_text(toml(selected))
                with self.assertRaises(ValueError):
                    Context.load(self.root)
        selected = self.ctx.config.data()
        selected["operations"] = {"test-unknown": {"after": [["extra"]]}}
        (self.root / "plugin.toml").write_text(toml(selected))
        with self.assertRaisesRegex(ValueError, "standard operation"):
            Context.load(self.root)

    def test_after_hooks_do_not_disable_standard_integration_checks(self) -> None:
        from jellyfin_plugin_toolkit.bindings import check_integrations

        selected = self.ctx.config.data()
        selected.update(
            {
                "validation": {
                    "groups": ["backend", "artifacts"],
                    "check_targets": ["tooling"],
                },
                "operations": {
                    "check-tooling": {"after": [["extra"]]},
                    "format-check-tooling": {"commands": [["custom-formatter"]]},
                },
            }
        )
        from jellyfin_plugin_toolkit.configuration import Configuration, Operation

        selected_model = Configuration.model_validate(selected)
        (self.root / "Directory.Build.props").write_text("msbuild/Plugin.props")
        with self.assertRaisesRegex(ValueError, "integration is missing"):
            check_integrations(self.root, selected_model)
        (self.root / "eslint.config.mjs").write_text("configs/eslint.mjs")
        (self.root / "pyproject.toml").write_text("configs/ruff.toml")
        check_integrations(self.root, selected_model)
        (self.root / "eslint.config.mjs").unlink()
        selected_model.operations["check-tooling"] = Operation(
            commands=[["custom-checker"]]
        )
        check_integrations(self.root, selected_model)

    def test_standard_tooling_discovery_can_import_the_verified_toolkit(self) -> None:
        tests = self.root / "tests/tooling"
        tests.mkdir(parents=True)
        (tests / "test_business.py").write_text(
            "import unittest\nfrom jellyfin_plugin_toolkit.context import Context\n"
            "class BusinessTests(unittest.TestCase):\n"
            "    def test_business(self):\n"
            '        self.assertEqual("example", Context.load().identity.id)\n'
        )
        tasks.test(self.ctx, "tooling", ["-k", "business"])
