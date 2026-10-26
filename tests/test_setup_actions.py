"""Setup preserves native ownership and never silently changes environment modes."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SetupActionsTests(unittest.TestCase):
    def test_locked_sync_modes_preserve_explicit_project_selection(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "uv"
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$JPT_CALLS"\n')
            executable.chmod(0o755)
            calls = root / "calls"
            for mode, suffix in [("development", []), ("runtime", ["--no-dev"])]:
                subprocess.run(
                    ["bash", str(ROOT / ".github/actions/setup-python/sync.sh")],
                    env={
                        **os.environ,
                        "PATH": str(root) + os.pathsep + os.environ["PATH"],
                        "JPT_CALLS": str(calls),
                        "JPT_PROJECT": str(root / "trusted project"),
                        "JPT_SETUP_MODE": mode,
                    },
                    check=True,
                )
                self.assertEqual(
                    [
                        "sync",
                        "--project",
                        str(root / "trusted project"),
                        "--locked",
                        *suffix,
                    ],
                    calls.read_text().splitlines(),
                )

    def test_invalid_sync_mode_cannot_install_dependencies(self) -> None:
        result = subprocess.run(
            ["bash", str(ROOT / ".github/actions/setup-python/sync.sh")],
            env={**os.environ, "JPT_SETUP_MODE": "arbitrary"},
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b"Expected development or runtime", result.stdout)

    def test_failed_synchronization_is_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "uv"
            executable.write_text("#!/bin/sh\nexit 42\n")
            executable.chmod(0o755)
            result = subprocess.run(
                ["bash", str(ROOT / ".github/actions/setup-python/sync.sh")],
                env={
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "JPT_PROJECT": str(root),
                    "JPT_SETUP_MODE": "development",
                },
                check=False,
            )
            self.assertEqual(42, result.returncode)
