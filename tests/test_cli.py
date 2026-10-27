"""CLI contracts independent of consumer configuration and shell interpretation."""

import io
import json
import os
import pty
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from typer import TyperException
from typer._click.core import Command

from jellyfin_plugin_toolkit.cli import app, main


def invoke(arguments: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(arguments)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_complete_help_discovery_needs_no_consumer(self) -> None:
        from typer.main import get_command

        def walk(command: Command, prefix: list[str]) -> Iterator[list[str]]:
            yield prefix
            for name, child in getattr(command, "commands", {}).items():
                yield from walk(child, [*prefix, name])

        with patch("jellyfin_plugin_toolkit.context.Context.load") as load:
            for command in walk(get_command(app), []):
                with self.subTest(command=command):
                    code, out, err = invoke([*command, "--help"])
                    self.assertEqual((code, err), (0, ""))
                    self.assertIn("Usage:", out)
            load.assert_not_called()
        self.assertNotIn("  internal ", invoke(["--help"])[1])
        for name in [
            "ci",
            "binding-pr",
            "toolkit-release",
            "package",
            "sandbox",
        ]:
            self.assertIn(name, invoke(["internal", "--help"])[1])

    def test_version_and_completion_need_no_configuration_or_credentials(self) -> None:
        with patch("jellyfin_plugin_toolkit.context.Context.load") as load:
            self.assertEqual(0, invoke(["--version"])[0])
            code, out, err = invoke(["--show-completion", "bash"])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("_jpt_completion", out)
            load.assert_not_called()

    def test_root_precedence_and_machine_output(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"JELLYFIN_PLUGIN_ROOT": directory}),
        ):
            for args, root in [
                (["paths"], Path(directory)),
                (
                    ["--root", directory + "/explicit", "paths"],
                    Path(directory) / "explicit",
                ),
            ]:
                code, out, err = invoke(args)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(str(root / ".build/jpt"), json.loads(out)["toolkit"])
            self.assertEqual(directory, os.environ["JELLYFIN_PLUGIN_ROOT"])
            with patch.dict(os.environ):
                os.environ.pop("JELLYFIN_PLUGIN_ROOT", None)
                self.assertEqual(
                    str(Path.cwd() / ".build/jpt"),
                    json.loads(invoke(["paths"])[1])["toolkit"],
                )

    def test_stale_binding_and_editor_maintenance_skip_consumer_setup(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("jellyfin_plugin_toolkit.context.Context.load") as load,
        ):
            self.assertEqual(0, invoke(["--root", directory, "toolkit", "recover"])[0])
            self.assertEqual(0, invoke(["--root", directory, "editor", "vscode"])[0])
            with patch("jellyfin_plugin_toolkit.bindings.plan", return_value=[]):
                self.assertEqual(
                    0, invoke(["--root", directory, "toolkit", "sync", "--check"])[0]
                )
            load.assert_not_called()

    def test_usage_errors_precede_work(self) -> None:
        invalid = [
            ["dev", "--port", "0"],
            ["internal", "ci", "release-receipt", "--run-id", "0"],
            ["dev", "--port", "65536"],
            ["dev", "--media-mount", "/tmp", "--clear-media-mounts"],
            ["build", "unknown"],
            ["build", "backend", "--release"],
            ["validate", "--full"],
            ["toolkit", "sync", "--apply", "--check"],
            ["release", "config", "--apply", "--check"],
            [
                "internal",
                "binding-pr",
                "prepare",
                "--repository",
                "a/b",
                "--proposal",
                "proposal",
            ],
            [
                "internal",
                "release-lock",
                "--repository",
                "a/b",
                "--pr",
                "0",
                "--bot",
                "bot",
            ],
            ["test", "browser", "--project", "workflow-chromium"],
            ["test", "browser", "--help"],
            ["test", "--", "--help"],
            ["paths", "--root", "/tmp"],
            ["sync-bindings"],
            ["ci"],
            ["_package"],
        ]
        with patch("jellyfin_plugin_toolkit.cli.consumer") as load:
            for args in invalid:
                with self.subTest(args=args):
                    code, out, err = invoke(args)
                    self.assertEqual(2, code)
                    self.assertEqual("", out)
                    self.assertIn("Error:", err)
                    self.assertNotIn("\x1b[", err)
            load.assert_not_called()

    def test_runner_tokens_are_preserved_after_first_separator(self) -> None:
        tokens = [
            "--help",
            "two words",
            "**/*.ts",
            "$(literal)",
            "`literal`",
            "--",
            "--root",
            "/runner",
        ]
        selected = Mock()
        with (
            patch("jellyfin_plugin_toolkit.cli.consumer", return_value=selected),
            patch("jellyfin_plugin_toolkit.tasks.test") as run,
        ):
            self.assertEqual(0, invoke(["test", "custom-target", "--", *tokens])[0])
            run.assert_called_once_with(selected, "custom-target", tokens)

    def test_failures_debug_and_child_status(self) -> None:
        with patch(
            "jellyfin_plugin_toolkit.maintenance.paths",
            side_effect=ValueError("Fix the configured path"),
        ):
            code, out, err = invoke(["paths"])
            self.assertEqual((code, out), (1, ""))
            self.assertEqual("Error: Fix the configured path\n", err)
            code, out, err = invoke(["--debug", "paths"])
            self.assertEqual((code, out), (1, ""))
            self.assertIn("Traceback (most recent call last)", err)
            self.assertNotIn("locals", err)
        for error, expected in [
            (subprocess.CalledProcessError(7, "runner"), 7),
            (subprocess.CalledProcessError(-15, "runner"), 143),
            (KeyboardInterrupt(), 130),
            (TyperException("Invalid command state"), 1),
        ]:
            with (
                self.subTest(error=error),
                patch("jellyfin_plugin_toolkit.maintenance.paths", side_effect=error),
            ):
                self.assertEqual(expected, invoke(["paths"])[0])

    def test_generation_does_not_load_a_consumer(self) -> None:
        with (
            patch("jellyfin_plugin_toolkit.scaffold.generate") as generate,
            patch("jellyfin_plugin_toolkit.context.Context.load") as load,
        ):
            self.assertEqual(
                0,
                invoke(
                    [
                        "new",
                        "/tmp/example",
                        "--id",
                        "example",
                        "--name",
                        "Example",
                        "--repository",
                        "owner/example",
                    ]
                )[0],
            )
            generate.assert_called_once()
            load.assert_not_called()

    def test_module_and_console_entries_and_terminal_help(self) -> None:
        environment = dict(os.environ, CI="true")
        for executable in [
            [sys.executable, "-I", "-m", "jellyfin_plugin_toolkit"],
            [str(Path(sys.executable).with_name("jpt"))],
        ]:
            result = subprocess.run(
                [*executable, "paths"],
                capture_output=True,
                text=True,
                env=environment,
                check=True,
            )
            self.assertIn("toolkit", json.loads(result.stdout))
            self.assertEqual("", result.stderr)
        environment.pop("CI", None)
        environment.pop("TYPER_USE_RICH", None)
        environment["TERM"] = "xterm-256color"
        master, slave = pty.openpty()
        try:
            child = subprocess.Popen(
                [sys.executable, "-m", "jellyfin_plugin_toolkit", "--help"],
                stdout=slave,
                stderr=slave,
                env=environment,
            )
            os.close(slave)
            data = b""
            while True:
                try:
                    part = os.read(master, 65536)
                except OSError:
                    break
                if not part:
                    break
                data += part
            self.assertEqual(0, child.wait(timeout=10))
            self.assertIn("╭", data.decode())
        finally:
            os.close(master)
