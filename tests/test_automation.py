"""Automation uses the configured App identity and never a synthetic author."""

import io
import json
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jellyfin_plugin_toolkit import automation, cli


class AutomationTests(unittest.TestCase):
    def test_identity_comes_from_verified_bot_account(self) -> None:
        payload: dict[str, Any] = {
            "login": "fixture-app[bot]",
            "id": 123,
            "type": "Bot",
        }
        with patch.object(
            urllib.request,
            "urlopen",
            return_value=io.BytesIO(json.dumps(payload).encode()),
        ) as request:
            identity = automation.resolve_bot("fixture-app")
        self.assertEqual(
            {
                "name": "fixture-app[bot]",
                "email": "123+fixture-app[bot]@users.noreply.github.com",
            },
            identity,
        )
        self.assertEqual(
            "https://api.github.com/users/fixture-app%5Bbot%5D",
            request.call_args.args[0].full_url,
        )

    def test_unexpected_accounts_are_rejected(self) -> None:
        for payload in [
            {"login": "other[bot]", "id": 123, "type": "Bot"},
            {"login": "fixture-app[bot]", "id": 123, "type": "User"},
            {"login": "fixture-app[bot]", "id": True, "type": "Bot"},
            {"login": "fixture-app[bot]", "id": 0, "type": "Bot"},
        ]:
            with (
                self.subTest(payload=payload),
                patch.object(
                    urllib.request,
                    "urlopen",
                    return_value=io.BytesIO(json.dumps(payload).encode()),
                ),
                self.assertRaises(ValueError),
            ):
                automation.resolve_bot("fixture-app")

    def test_missing_token_or_slug_stops_before_network_access(self) -> None:
        for environment in [{}, {"GH_TOKEN": "fixture-token"}]:
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(urllib.request, "urlopen") as network,
            ):
                with self.assertRaises(ValueError):
                    automation.commit_identity()
                network.assert_not_called()

    def test_new_needs_no_app_identity(self) -> None:
        with (
            patch("jellyfin_plugin_toolkit.scaffold.generate") as generate,
            patch.object(automation, "resolve_bot") as lookup,
        ):
            cli.main(
                [
                    "new",
                    "/tmp/example",
                    "--id",
                    "example",
                    "--name",
                    "Example",
                    "--repository",
                    "example/plugin",
                ]
            )
        self.assertNotIn("app_slug", generate.call_args.kwargs)
        lookup.assert_not_called()

    def test_renovate_must_recognize_actual_app(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identity: dict[str, Any] = {
                "name": "fixture-app[bot]",
                "email": "123+fixture-app[bot]@users.noreply.github.com",
            }
            state = root / automation.STATE_PATH
            state.parent.mkdir()
            state.write_text(json.dumps(automation.identity_record(identity)))
            for emails, valid in [
                ([], False),
                (["unrelated@example.invalid", identity["email"]], True),
            ]:
                (root / "renovate.json").write_text(
                    json.dumps({"gitIgnoredAuthors": emails})
                )
                if valid:
                    automation.check_renovate(root, identity)
                else:
                    with self.assertRaises(ValueError):
                        automation.check_renovate(root, identity)


class WorkflowIdentityTests(unittest.TestCase):
    def test_draft_inspection_uses_the_app_token_with_push_access(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename, step in [
            ("toolkit-release-please.yml", "Resolve the matching unpublished draft"),
            ("prepare-release.yml", "Resume an existing matching unpublished draft"),
        ]:
            with self.subTest(workflow=filename):
                workflow = (root / ".github/workflows" / filename).read_text()
                inspection = workflow.split("- name: " + step, 1)[1].split("run:", 1)[0]
                self.assertIn("GH_TOKEN: ${{ steps.app.outputs.token }}", inspection)
                self.assertNotIn("github.token", inspection)
                self.assertIn("permission-contents: write", workflow)

    def test_writers_require_app_tokens(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename in [
            "prepare-release.yml",
            "toolkit-release-please.yml",
            "sync-bindings.yml",
            "release.yml",
            "catalog-notify.yml",
            "setup-automation.yml",
        ]:
            text = (root / ".github/workflows" / filename).read_text()
            with self.subTest(workflow=filename):
                self.assertIn("/.github/actions/app-token@", text)
                self.assertNotIn("steps.app.outputs.token ||", text)
                self.assertNotIn("if: inputs.app_client_id !=", text)
                self.assertIn("${{ steps.app.outputs.token }}", text)
                self.assertNotRegex(text, r"(?m)^ +contents: write$")
        from jellyfin_plugin_toolkit.workflows import render

        callers = render("example/toolkit", "a" * 40, version="v0.3.0")
        for filename in ["release.yml", "release-please.yml"]:
            self.assertIn(
                "app_client_id: ${{ vars.PLUGIN_APP_CLIENT_ID }}", callers[filename]
            )
            self.assertIn("PLUGIN_APP_PRIVATE_KEY:", callers[filename])

    def test_shared_preset_does_not_invent_an_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename in [
            "default.json",
            "src/jellyfin_plugin_toolkit/resources/default.json",
        ]:
            self.assertNotIn(
                "gitIgnoredAuthors", json.loads((root / filename).read_text())
            )


class TrackedConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.state = self.root / automation.STATE_PATH
        self.state.parent.mkdir()
        (self.root / "renovate.json").write_text("{}")
        (self.root / ".github/workflows").mkdir()
        (self.root / ".github/workflows/app-setup.yml").write_text(
            "on: workflow_dispatch\n"
        )
        self.identity = {
            "name": "fixture-app[bot]",
            "email": "123+fixture-app[bot]@users.noreply.github.com",
        }

    def record(self) -> None:
        self.state.write_text(json.dumps(automation.identity_record(self.identity)))
        (self.root / "renovate.json").write_text(
            json.dumps({"gitIgnoredAuthors": [self.identity["email"]]})
        )

    def test_missing_record_is_offline_notice_but_blocks_app_writes(self) -> None:
        with (
            patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/plugin"}, clear=True),
            patch.object(urllib.request, "urlopen") as network,
            patch("builtins.print") as output,
        ):
            automation.check_configuration(self.root)
            self.assertIn("NOTICE:", output.call_args.args[0])
            with self.assertRaisesRegex(
                ValueError,
                "https://github.com/example/plugin/actions/workflows/app-setup.yml",
            ):
                automation.check_renovate(self.root, self.identity)
            network.assert_not_called()

    def test_current_record_needs_no_credentials_or_network(self) -> None:
        self.record()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(urllib.request, "urlopen") as network,
        ):
            automation.check_configuration(self.root)
            automation.check_renovate(self.root, self.identity)
        network.assert_not_called()

    def test_malformed_record_requires_tracked_repair_without_rewriting(self) -> None:
        for value in [
            "not json",
            "[]",
            '{"schema_version":1}',
            '{"schema_version":true}',
        ]:
            with self.subTest(value=value):
                self.state.write_text(value)
                with self.assertRaisesRegex(ValueError, "Repair or restore"):
                    automation.check_configuration(self.root)
                self.assertEqual(value, self.state.read_text())

    def test_stale_live_identity_requires_setup_but_static_checks_stay_offline(
        self,
    ) -> None:
        self.record()
        automation.check_configuration(self.root)
        with self.assertRaisesRegex(ValueError, "Connected App identity differs"):
            automation.check_renovate(
                self.root,
                {
                    "name": "replacement[bot]",
                    "email": "456+replacement[bot]@users.noreply.github.com",
                },
            )

    def test_existing_renovate_drift_fails_static_checks(self) -> None:
        self.record()
        for configuration, message in [
            ({}, "managed App identity"),
            ({"gitIgnoredAuthors": "wrong shape"}, "repair renovate.json"),
        ]:
            with self.subTest(configuration=configuration):
                (self.root / "renovate.json").write_text(json.dumps(configuration))
                with self.assertRaisesRegex(ValueError, message):
                    automation.check_configuration(self.root)
