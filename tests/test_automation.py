"""Automation uses the configured App identity and never a synthetic author."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jellyfin_plugin_toolkit import automation, cli


class AutomationTests(unittest.TestCase):
    def test_identity_comes_from_verified_bot_account(self):
        payload = {"login": "fixture-app[bot]", "id": 123, "type": "Bot"}
        with patch.object(
            automation.urllib.request,
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

    def test_unexpected_accounts_are_rejected(self):
        for payload in [
            {"login": "other[bot]", "id": 123, "type": "Bot"},
            {"login": "fixture-app[bot]", "id": 123, "type": "User"},
            {"login": "fixture-app[bot]", "id": True, "type": "Bot"},
            {"login": "fixture-app[bot]", "id": 0, "type": "Bot"},
        ]:
            with (
                self.subTest(payload=payload),
                patch.object(
                    automation.urllib.request,
                    "urlopen",
                    return_value=io.BytesIO(json.dumps(payload).encode()),
                ),
            ):
                with self.assertRaises(ValueError):
                    automation.resolve_bot("fixture-app")

    def test_missing_token_or_slug_stops_before_network_access(self):
        for environment in [{}, {"GH_TOKEN": "fixture-token"}]:
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(automation.urllib.request, "urlopen") as network,
            ):
                with self.assertRaises(ValueError):
                    automation.commit_identity()
                network.assert_not_called()

    def test_new_needs_no_app_identity(self):
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

    def test_renovate_must_recognize_actual_app(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identity = {
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
    def test_draft_inspection_uses_the_app_token_with_push_access(self):
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

    def test_writers_require_app_tokens(self):
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
                self.assertIn("actions/create-github-app-token@", text)
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

    def test_shared_preset_does_not_invent_an_identity(self):
        root = Path(__file__).resolve().parents[1]
        for filename in [
            "default.json",
            "src/jellyfin_plugin_toolkit/resources/default.json",
        ]:
            self.assertNotIn(
                "gitIgnoredAuthors", json.loads((root / filename).read_text())
            )
