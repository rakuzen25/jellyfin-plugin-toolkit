"""Public command defaults, argument forwarding and failure propagation."""

import subprocess
from unittest.mock import call, patch

from consumer_fixture import ConsumerTestCase

from jellyfin_plugin_toolkit import tasks
from jellyfin_plugin_toolkit.cli import main
from jellyfin_plugin_toolkit.context import Context


class CommandTests(ConsumerTestCase):
    def test_default_tests_do_not_run_acceptance(self):
        with patch.object(tasks, "test") as test:
            main(["test"])
        self.assertEqual(
            [call(self.ctx, "backend", []), call(self.ctx, "tooling", [])],
            test.call_args_list,
        )

    def test_browser_arguments_are_forwarded_literally(self):
        with patch.object(tasks, "test") as test:
            main(
                [
                    "test",
                    "browser",
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

    def test_check_stops_after_first_failure(self):
        with (
            patch.object(
                tasks, "check", side_effect=subprocess.CalledProcessError(1, "check")
            ) as check,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            main(["check"])
        self.assertEqual(1, check.call_count)

    def test_docs_checks_format_before_links_without_installing(self):
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

    def test_operations_use_argv_and_preserve_globs(self):
        context = Context.load(self.root)
        selected = dict(context.config)
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
                    Context(self.root, selected), "test-example", ["--workers=2"]
                )
            )
        self.assertEqual(
            ("fixture.py", "**/*.{ts,mjs}", "$(literal)", "--workers=2"),
            run.call_args.args[1:],
        )
        self.assertEqual(self.root, run.call_args.kwargs["cwd"])

    def test_bootstrap_venv_can_resolve_the_verified_toolkit(self):
        with (
            patch.object(tasks, "verify_tools"),
            patch.object(Context, "generated"),
            patch.object(tasks, "prepare"),
            patch("pathlib.Path.exists", return_value=True),
            patch.object(tasks, "run") as run,
        ):
            tasks.bootstrap(self.ctx)
        pip = next(
            invocation
            for invocation in run.call_args_list
            if "jellyfin_plugin_toolkit.checks.check_pip" in invocation.args
        )
        self.assertIn(
            str(tasks.TOOLKIT_ROOT),
            pip.kwargs["env"]["PYTHONPATH"].split(__import__("os").pathsep),
        )

    def test_backend_tests_prepare_missing_embedded_resources_without_installing(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = SimpleNamespace(
                root=root,
                config={
                    "frontend": {"enabled": True, "required_assets": ["config.html"]},
                    "backend": {"tests": []},
                },
                generated=Mock(),
            )
            with (
                patch.object(tasks, "frontend_build") as build,
                patch.object(tasks, "run") as run,
            ):
                tasks.backend_tests(context, [])
                build.assert_called_once_with(context)
                run.assert_not_called()
                (root / "dist/web").mkdir(parents=True)
                (root / "dist/web/config.html").write_text("verified prebuilt resource")
                build.reset_mock()
                tasks.backend_tests(context, [])
                build.assert_not_called()

    def test_common_tooling_checks_never_restore_dependencies(self):
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

    def test_tooling_check_preserves_consumer_formatting_override(self):
        self.ctx.config["operations"] = {
            "format-check-tooling": {"commands": [["formatter", "--verify"]]}
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
