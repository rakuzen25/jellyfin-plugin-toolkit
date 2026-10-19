"""Public command defaults, argument forwarding and failure propagation."""

import subprocess
import unittest
from unittest.mock import call, patch

from consumer_fixture import ROOT

from jellyfin_plugin_toolkit import tasks
from jellyfin_plugin_toolkit.cli import main
from jellyfin_plugin_toolkit.context import Context


class CommandTests(unittest.TestCase):
    def test_default_tests_do_not_run_acceptance(self):
        with patch.object(tasks, "test") as test:
            main(["test"])
        self.assertEqual(
            [call("backend", []), call("tooling", [])], test.call_args_list
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
            "browser", ["--project", "workflow-chromium", "--grep", "literal $(value)"]
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
            patch.object(tasks, "run") as run,
            patch("jellyfin_plugin_toolkit.checks.docs.main") as links,
        ):
            tasks.check("docs")
        links.assert_called_once()
        self.assertIn("--check", run.call_args.args)
        self.assertNotIn("install", run.call_args.args)
        with (
            patch.object(
                tasks, "run", side_effect=subprocess.CalledProcessError(1, "prettier")
            ),
            patch("jellyfin_plugin_toolkit.checks.docs.main") as links,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            tasks.check("docs")
        links.assert_not_called()

    def test_operations_use_argv_and_preserve_globs(self):
        context = Context.load(ROOT)
        selected = dict(context.config)
        selected["operations"] = {
            "test-example": {
                "commands": [["{python}", "fixture.py", "**/*.{ts,mjs}", "$(literal)"]]
            }
        }
        with (
            patch.object(Context, "load", return_value=Context(ROOT, selected)),
            patch.object(tasks, "run") as run,
        ):
            self.assertTrue(tasks.operation("test-example", ["--workers=2"]))
        self.assertEqual(
            ("fixture.py", "**/*.{ts,mjs}", "$(literal)", "--workers=2"),
            run.call_args.args[1:],
        )
        self.assertEqual(ROOT, run.call_args.kwargs["cwd"])

    def test_bootstrap_venv_can_resolve_the_verified_toolkit(self):
        with (
            patch.object(tasks, "verify_tools"),
            patch.object(Context, "generated"),
            patch.object(tasks, "prepare"),
            patch("pathlib.Path.exists", return_value=True),
            patch.object(tasks, "run") as run,
        ):
            tasks.bootstrap()
        pip = next(
            invocation
            for invocation in run.call_args_list
            if "jellyfin_plugin_toolkit.checks.check_pip" in invocation.args
        )
        self.assertIn(
            str(tasks.TOOLKIT_ROOT),
            pip.kwargs["env"]["PYTHONPATH"].split(__import__("os").pathsep),
        )
