"""The privileged writer only accepts recomputed changes for an eligible PR head."""

import copy
import unittest
from unittest.mock import patch

import test_bindings as fixtures

from jellyfin_plugin_toolkit import binding_pr


class BindingPRTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.BindingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        fixture.upgrade()
        fixture.git("add", "toolkit.lock.json")
        fixture.git(
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.org",
            "commit",
            "-qm",
            "update",
        )
        head = fixture.git("rev-parse", "HEAD").decode().strip()
        fixture.git("checkout", "--detach", "-q", fixture.base)
        self.pr = {
            "number": 17,
            "state": "open",
            "user": {"login": "renovate[bot]", "type": "Bot"},
            "base": {
                "sha": fixture.base,
                "ref": "main",
                "repo": {"full_name": "example/plugin"},
            },
            "head": {
                "sha": head,
                "ref": "renovate/toolkit",
                "repo": {"full_name": "example/plugin"},
            },
        }
        self.bot = "renovate[bot]"

    def prepare(self):
        # Only the fetch needs a network substitute; every Git snapshot and release check is real.
        original = binding_pr.subprocess.check_output

        def execute(command, **kwargs):
            if "fetch" in command:
                return b""
            return original(command, **kwargs)

        with patch.object(binding_pr.subprocess, "check_output", side_effect=execute):
            return binding_pr.prepare(self.root, "example/plugin", self.pr, self.bot)

    def test_preparation_reads_head_as_data_and_preserves_checkout(self):
        before = (self.root / "plugin").read_bytes()
        proposal = self.prepare()
        self.assertTrue(proposal["changes"])
        self.assertEqual(before, (self.root / "plugin").read_bytes())
        self.assertEqual(self.pr["head"]["sha"], proposal["head"])

    def test_forks_humans_closed_prs_and_different_bots_are_rejected(self):
        for field, value in [
            ("state", "closed"),
            ("user", {"login": "someone", "type": "User"}),
        ]:
            pr = {**self.pr, field: value}
            self.assertFalse(binding_pr.eligible(pr, "example/plugin", self.bot))
        pr = copy.deepcopy(self.pr)
        pr["head"]["repo"]["full_name"] = "fork/plugin"
        self.assertFalse(binding_pr.eligible(pr, "example/plugin", self.bot))
        self.assertFalse(binding_pr.eligible(self.pr, "example/plugin", "another[bot]"))

    def test_failed_validation_can_still_trigger_preparation(self):
        run = {
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "head_sha": self.pr["head"]["sha"],
        }
        with patch.object(binding_pr, "api", side_effect=[run, [self.pr]]):
            self.assertEqual(
                self.pr, binding_pr.discover("example/plugin", self.bot, run_id=12)
            )

    def test_stale_validation_does_not_update_newer_pr_head(self):
        run = {"event": "pull_request", "status": "completed", "head_sha": "c" * 40}
        with patch.object(binding_pr, "api", side_effect=[run, [self.pr]]):
            self.assertIsNone(
                binding_pr.discover("example/plugin", self.bot, run_id=12)
            )

    def test_tampered_proposal_is_rejected_before_writes(self):
        proposal = self.prepare()
        tampered = copy.deepcopy(proposal)
        tampered["changes"][0]["name"] = "src/application.py"
        with (
            patch.object(binding_pr, "api", return_value=self.pr) as api,
            patch.object(binding_pr, "prepare", return_value=proposal),
        ):
            with self.assertRaisesRegex(ValueError, "altered"):
                binding_pr.commit(self.root, "example/plugin", tampered, self.bot)
            self.assertEqual(1, api.call_count)

    def test_writer_creates_one_fast_forward_commit_without_replacing_tree(self):
        proposal = self.prepare()
        calls = []

        def api(repository, endpoint, method="GET", body=None):
            calls.append((endpoint, method, body))
            if endpoint.startswith("pulls/"):
                return self.pr
            if endpoint.startswith("git/commits/"):
                return {"tree": {"sha": "original-tree"}}
            if endpoint == "git/commits":
                return {"sha": "new-commit"}
            return {"sha": "object"}

        with (
            patch.object(binding_pr, "api", side_effect=api),
            patch.object(binding_pr, "prepare", return_value=proposal),
        ):
            binding_pr.commit(self.root, "example/plugin", proposal, self.bot)
        tree = next(body for endpoint, _, body in calls if endpoint == "git/trees")
        self.assertEqual("original-tree", tree["base_tree"])
        self.assertEqual(
            (
                "git/refs/heads/renovate/toolkit",
                "PATCH",
                {"sha": "new-commit", "force": False},
            ),
            calls[-1],
        )
        self.assertEqual(1, sum(endpoint == "git/commits" for endpoint, _, _ in calls))

    def test_head_change_prevents_branch_write(self):
        proposal = self.prepare()
        changed = copy.deepcopy(self.pr)
        changed["head"]["sha"] = "c" * 40
        with (
            patch.object(binding_pr, "api", return_value=changed) as api,
            patch.object(
                binding_pr, "prepare", return_value={**proposal, "head": "c" * 40}
            ),
        ):
            with self.assertRaisesRegex(ValueError, "changed"):
                binding_pr.commit(self.root, "example/plugin", proposal, self.bot)
            self.assertEqual(1, api.call_count)
