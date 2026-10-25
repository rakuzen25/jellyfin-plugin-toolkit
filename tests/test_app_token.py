"""Native App token bootstrap checks never need credentials or project tooling."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ACTION = Path(__file__).resolve().parents[1] / ".github/actions/app-token"


class AppTokenTests(unittest.TestCase):
    def execute(
        self, script: str = "validate.sh", **changes: str
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as folder:
            summary = Path(folder) / "summary"
            environment = {
                "PATH": os.environ["PATH"],
                "APP_CLIENT_ID": "fixture-client",
                "APP_PRIVATE_KEY": "private-key-must-not-be-printed",
                "APP_OWNER": "",
                "APP_REPOSITORIES": "",
                "APP_PERMISSION_CONTENTS": "read",
                "GITHUB_STEP_SUMMARY": str(summary),
                **changes,
            }
            result = subprocess.run(
                ["bash", str(ACTION / script)],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            report = summary.read_text() if summary.exists() else ""
            if environment["APP_PRIVATE_KEY"].strip():
                self.assertNotIn(
                    environment["APP_PRIVATE_KEY"],
                    result.stdout + result.stderr + report,
                )
            return result, report

    def test_missing_credentials_fail_with_recovery_without_token_creation(
        self,
    ) -> None:
        for name in ["APP_CLIENT_ID", "APP_PRIVATE_KEY"]:
            with self.subTest(name=name):
                result, report = self.execute(**{name: " "})
                self.assertNotEqual(0, result.returncode)
                self.assertIn("PLUGIN_APP_CLIENT_ID", result.stdout)
                self.assertIn("App setup cannot repair credentials", report)

    def test_explicit_repository_scopes_and_permissions(self) -> None:
        for settings, valid in [
            ({}, True),
            ({"APP_OWNER": "example"}, False),
            ({"APP_OWNER": "example", "APP_REPOSITORIES": "one\ntwo"}, True),
            ({"APP_OWNER": "example", "APP_REPOSITORIES": ", \n"}, False),
            ({"APP_PERMISSION_CONTENTS": ""}, False),
            ({"APP_PERMISSION_CONTENTS": "admin"}, False),
            ({"APP_PERMISSION_WORKFLOWS": "read"}, False),
            (
                {"APP_PERMISSION_CONTENTS": "", "APP_PERMISSION_WORKFLOWS": "write"},
                True,
            ),
        ]:
            with self.subTest(settings=settings):
                result, _ = self.execute(**settings)
                self.assertEqual(valid, result.returncode == 0)

    def test_authentication_failure_remains_a_failure_and_explains_recovery(
        self,
    ) -> None:
        result, report = self.execute("failure.sh")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("original GitHub error", report)
        self.assertIn("installation", report)
        self.assertIn("permissions", report)
        self.assertNotIn("run App setup", report)

    def test_native_token_outputs_permissions_and_revocation_are_preserved(
        self,
    ) -> None:
        action = (ACTION / "action.yml").read_text()
        self.assertIn("uses: actions/create-github-app-token@", action)
        self.assertNotIn("skip-token-revoke", action)
        for output in ["token", "app-slug", "installation-id"]:
            self.assertIn("value: ${{ steps.mint.outputs." + output + " }}", action)
        for permission in [
            "contents",
            "pull-requests",
            "issues",
            "actions",
            "attestations",
            "workflows",
        ]:
            self.assertIn(
                "permission-"
                + permission
                + ": ${{ inputs.permission-"
                + permission
                + " }}",
                action,
            )
        self.assertIn("steps.mint.outcome == 'failure'", action)
        # Inner failure is reported and re-raised, allowing the caller's existing
        # continue-on-error policy to decide whether the job may continue.
        self.assertIn("continue-on-error: true", action)
        self.assertIn('run: bash "$APP_ACTION_PATH/failure.sh"', action)
