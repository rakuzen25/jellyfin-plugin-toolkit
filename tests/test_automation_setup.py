"""App setup owns a bounded PR, preserves other authors, and respects review."""

import base64
import copy
import json
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jellyfin_plugin_toolkit import automation
from jellyfin_plugin_toolkit import automation_setup as setup

IDENTITY: dict[str, Any] = {
    "name": "fixture-app[bot]",
    "email": "123+fixture-app[bot]@users.noreply.github.com",
}
RECORD: dict[str, Any] = {"schema_version": 1, "app_slug": "fixture-app", "bot_id": 123}
BASE = "a" * 40
HEAD = "b" * 40
REPOSITORY = "example/plugin"


def encoded(data: bytes) -> dict[str, Any]:
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(data).decode(),
    }


def formatted(root: Path | None, filename: str, data: dict[str, Any]) -> bytes:
    return (json.dumps(data, sort_keys=True) + "\n").encode()


class Repository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.base = BASE
        self.head: str | None = None
        self.pulls: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str, dict[str, Any]]] = []
        self.base_files: dict[str, Any] = {
            "renovate.json": json.dumps(
                {
                    "extends": ["config:recommended"],
                    "gitIgnoredAuthors": ["human@example.invalid"],
                }
            ).encode()
        }
        self.head_files: dict[str, bytes] = {}
        self.root.joinpath("renovate.json").write_bytes(
            self.base_files["renovate.json"]
        )
        self.comparison: dict[str, Any] = {
            "total_commits": 0,
            "commits": [],
            "files": [],
            "merge_base_commit": {"sha": BASE},
        }
        self.change_on_recheck = False
        self.base_reads = 0

    def existing_pr(self, closed: bool = False) -> None:
        self.head = HEAD
        configuration = setup.configuration_update(
            json.loads(self.base_files["renovate.json"]), None, IDENTITY
        )
        self.head_files = {
            "renovate.json": formatted(None, "renovate.json", configuration),
            automation.STATE_PATH: formatted(None, automation.STATE_PATH, RECORD),
        }
        self.pulls = [
            {
                "number": 1,
                "state": "closed" if closed else "open",
                "merged_at": None,
                "body": setup.BODY,
                "user": {"login": IDENTITY["name"], "type": "Bot"},
                "html_url": "https://github.com/example/plugin/pull/1",
                "base": {"ref": "main"},
                "head": {"sha": HEAD, "repo": {"full_name": REPOSITORY}},
            }
        ]
        self.comparison = {
            "total_commits": 1,
            "commits": [
                {
                    "commit": {"author": IDENTITY, "committer": IDENTITY},
                    "author": {"type": "Bot"},
                    "committer": {"type": "Bot"},
                }
            ],
            "files": [
                {
                    "filename": name,
                    "status": "added" if name != "renovate.json" else "modified",
                }
                for name in setup.FILES
            ],
            "merge_base_commit": {"sha": BASE},
        }

    def api(
        self,
        repository: str,
        endpoint: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        if repository != REPOSITORY:
            raise AssertionError(repository)
        if method != "GET":
            assert body is not None
            self.writes.append((endpoint, method, body))
            if endpoint == "pulls":
                return {"html_url": "https://github.com/example/plugin/pull/2"}
            return {"sha": "c" * 40}
        if endpoint == "":
            return {"default_branch": "main"}
        if endpoint == "git/ref/heads/main":
            self.base_reads += 1
            return {
                "object": {
                    "sha": "d" * 40
                    if self.change_on_recheck and self.base_reads > 1
                    else self.base
                }
            }
        if endpoint.startswith("pulls?"):
            return self.pulls
        if endpoint.startswith("git/matching-refs/"):
            return (
                [{"ref": "refs/heads/" + setup.BRANCH, "object": {"sha": self.head}}]
                if self.head
                else []
            )
        if endpoint.startswith("compare/"):
            return self.comparison
        if endpoint.startswith("git/commits/"):
            return {"tree": {"sha": "tree-" + endpoint.split("/")[-1]}}
        if endpoint.startswith("git/trees/"):
            revision = endpoint.split("tree-", 1)[1].split("?", 1)[0]
            files = self.head_files if revision == HEAD else self.base_files
            if revision == BASE:
                files = {
                    name: (self.root / name).read_bytes()
                    for name in setup.FILES
                    if (self.root / name).exists()
                }
            return {
                "tree": [
                    {"path": name, "mode": "100644", "type": "blob"} for name in files
                ]
            }
        if endpoint.startswith("contents/"):
            name, revision = endpoint.removeprefix("contents/").split("?ref=")
            if revision == BASE:
                return encoded((self.root / name).read_bytes())
            return encoded(
                (self.head_files if revision == HEAD else self.base_files)[name]
            )
        raise AssertionError(endpoint)


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.repository = Repository(self.root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.object(setup, "commit_identity", return_value=IDENTITY)
        )
        self.stack.enter_context(
            patch.object(setup, "api", side_effect=self.repository.api)
        )
        self.stack.enter_context(
            patch.object(subprocess, "check_output", return_value=BASE + "\n")
        )
        self.stack.enter_context(
            patch.object(setup, "format_json", side_effect=formatted)
        )

    def run_setup(self, retry: bool = False) -> None:
        setup.setup(self.root, REPOSITORY, retry)

    def test_initial_pr_only_writes_two_files_and_never_default_branch(self) -> None:
        self.run_setup()
        writes = self.repository.writes
        tree = next(body for endpoint, _, body in writes if endpoint == "git/trees")
        self.assertEqual(setup.FILES, {item["path"] for item in tree["tree"]})
        commit = next(body for endpoint, _, body in writes if endpoint == "git/commits")
        self.assertEqual(IDENTITY, commit["author"])
        self.assertEqual(IDENTITY, commit["committer"])
        self.assertEqual([BASE], commit["parents"])
        ref = next(body for endpoint, _, body in writes if endpoint == "git/refs")
        self.assertEqual("refs/heads/" + setup.BRANCH, ref["ref"])
        pr = next(body for endpoint, _, body in writes if endpoint == "pulls")
        self.assertEqual("main", pr["base"])

    def test_merged_configuration_is_a_noop(self) -> None:
        state = self.root / automation.STATE_PATH
        state.parent.mkdir()
        state.write_text(json.dumps(RECORD))
        configuration = json.loads((self.root / "renovate.json").read_text())
        configuration["gitIgnoredAuthors"].append(IDENTITY["email"])
        (self.root / "renovate.json").write_text(json.dumps(configuration))
        self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_pending_pr_is_a_noop(self) -> None:
        self.repository.existing_pr()
        self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_closed_pr_waits_for_manual_retry(self) -> None:
        self.repository.existing_pr(closed=True)
        self.run_setup()
        self.assertEqual([], self.repository.writes)
        self.run_setup(retry=True)
        self.assertIn(("pulls/1", "PATCH", {"state": "open"}), self.repository.writes)
        self.assertNotIn(
            "pulls", [endpoint for endpoint, _, _ in self.repository.writes]
        )

    def test_manual_retry_can_replace_a_deleted_branch(self) -> None:
        self.repository.existing_pr(closed=True)
        self.repository.head = None
        self.run_setup(retry=True)
        self.assertIn("pulls", [endpoint for endpoint, _, _ in self.repository.writes])
        self.assertNotIn(
            "pulls/1", [endpoint for endpoint, _, _ in self.repository.writes]
        )

    def test_identity_lookup_failure_prevents_all_writes(self) -> None:
        with (
            patch.object(
                setup, "commit_identity", side_effect=OSError("lookup failed")
            ),
            self.assertRaises(OSError),
        ):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_malformed_base_record_prevents_all_writes(self) -> None:
        state = self.root / automation.STATE_PATH
        state.parent.mkdir()
        state.write_text('{"schema_version": 2}')
        with self.assertRaises(ValueError):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_format_failure_prevents_all_writes(self) -> None:
        with (
            patch.object(
                setup, "format_json", side_effect=ValueError("formatter failed")
            ),
            self.assertRaisesRegex(ValueError, "formatter failed"),
        ):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_reviewed_history_can_survive_squash_merges_and_app_rotation(self) -> None:
        self.repository.existing_pr()
        # Old commits reviewed in a merged setup PR need not belong to the
        # currently connected App. Newly added commits still must belong to it.
        reviewed = "f" * 40
        merged = copy.deepcopy(self.repository.pulls[0])
        merged.update(number=0, state="closed", merged_at="2026-10-21T00:00:00Z")
        merged["head"]["sha"] = reviewed
        merged["user"]["login"] = "older-app[bot]"
        self.repository.pulls.append(merged)
        self.repository.comparison["commits"][0]["committer"] = {"type": "User"}
        original = self.repository.api

        def api(
            repository: str,
            endpoint: str,
            method: str = "GET",
            body: dict[str, Any] | None = None,
        ) -> Any:
            if endpoint == "compare/" + reviewed + "..." + HEAD:
                return {
                    "merge_base_commit": {"sha": reviewed},
                    "commits": [],
                    "total_commits": 0,
                }
            return original(repository, endpoint, method, body)

        with patch.object(setup, "api", side_effect=api):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_base_race_stops_before_writes(self) -> None:
        self.repository.change_on_recheck = True
        with self.assertRaisesRegex(ValueError, "base or branch changed"):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_wrong_checkout_stops_before_writes(self) -> None:
        self.repository.base = "d" * 40
        with self.assertRaisesRegex(ValueError, "Default branch moved"):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_human_branch_commit_is_not_overwritten(self) -> None:
        self.repository.existing_pr()
        self.repository.comparison["commits"][0]["committer"] = {"type": "User"}
        with self.assertRaisesRegex(ValueError, "human or unrecognized"):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_edit_inside_allowed_file_is_not_overwritten(self) -> None:
        self.repository.existing_pr()
        proposed = json.loads(self.repository.head_files["renovate.json"])
        proposed["automerge"] = True
        self.repository.head_files["renovate.json"] = json.dumps(proposed).encode()
        with self.assertRaisesRegex(ValueError, "configuration was edited"):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_unrelated_branch_file_is_not_overwritten(self) -> None:
        self.repository.existing_pr()
        self.repository.comparison["files"].append(
            {"filename": "pyproject.toml", "status": "modified"}
        )
        with self.assertRaisesRegex(ValueError, "unrelated changes"):
            self.run_setup()
        self.assertEqual([], self.repository.writes)

    def test_existing_branch_update_is_fast_forward_and_preserves_main(self) -> None:
        self.repository.existing_pr()
        # Simulate a new main revision while the setup proposal is still valid
        # relative to its original merge base.
        self.repository.comparison["merge_base_commit"]["sha"] = "e" * 40
        self.run_setup()
        commit = next(
            body
            for endpoint, _, body in self.repository.writes
            if endpoint == "git/commits"
        )
        self.assertEqual([HEAD, BASE], commit["parents"])
        self.assertIn(
            (
                "git/refs/heads/" + setup.BRANCH,
                "PATCH",
                {"sha": "c" * 40, "force": False},
            ),
            self.repository.writes,
        )


class ConfigurationTests(unittest.TestCase):
    def test_pinned_formatter_emits_json_without_writing_checkout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        destination = root / automation.STATE_PATH
        before = destination.read_bytes() if destination.exists() else None
        output = setup.format_json(root, automation.STATE_PATH, RECORD)
        self.assertEqual(RECORD, json.loads(output))
        self.assertEqual(
            before, destination.read_bytes() if destination.exists() else None
        )

    def test_rotation_replaces_only_owned_email(self) -> None:
        old: dict[str, Any] = {
            "schema_version": 1,
            "app_slug": "previous-app",
            "bot_id": 456,
        }
        old_email = automation.record_identity(old)["email"]
        configuration: dict[str, Any] = {
            "extends": ["config:recommended"],
            "gitIgnoredAuthors": [
                "unrelated@example.invalid",
                old_email,
                "789+other-app[bot]@users.noreply.github.com",
            ],
        }
        before = copy.deepcopy(configuration)
        updated = setup.configuration_update(configuration, old, IDENTITY)
        self.assertEqual(
            [
                "unrelated@example.invalid",
                "789+other-app[bot]@users.noreply.github.com",
                IDENTITY["email"],
            ],
            updated["gitIgnoredAuthors"],
        )
        self.assertEqual(before, configuration)

    def test_current_identity_preserves_existing_author_order(self) -> None:
        configuration: dict[str, Any] = {
            "gitIgnoredAuthors": [IDENTITY["email"], "unrelated@example.invalid"]
        }
        self.assertEqual(
            configuration, setup.configuration_update(configuration, RECORD, IDENTITY)
        )

    def test_malformed_records_rejected(self) -> None:
        for state in [
            {},
            dict(RECORD, bot_id=True),
            dict(RECORD, bot_id=0),
            dict(RECORD, schema_version=2),
            dict(RECORD, schema_version=True),
            dict(RECORD, extra=True),
        ]:
            with self.subTest(state=state), self.assertRaises(ValueError):
                setup.configuration_update({}, state, IDENTITY)

    def test_invalid_authors_rejected(self) -> None:
        for authors in ["bot@example.invalid", [123]]:
            with self.assertRaises(ValueError):
                setup.configuration_update(
                    {"gitIgnoredAuthors": authors}, None, IDENTITY
                )

    def test_sync_requires_both_record_and_current_email(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "renovate.json").write_text(
                json.dumps({"gitIgnoredAuthors": [IDENTITY["email"]]})
            )
            with self.assertRaisesRegex(ValueError, "App setup"):
                automation.check_renovate(root, IDENTITY)
            state = root / automation.STATE_PATH
            state.parent.mkdir()
            state.write_text(json.dumps(RECORD))
            automation.check_renovate(root, IDENTITY)
            state.write_text(json.dumps(dict(RECORD, bot_id=456)))
            with self.assertRaisesRegex(ValueError, "App setup"):
                automation.check_renovate(root, IDENTITY)
