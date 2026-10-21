import copy
import unittest
from unittest.mock import patch

from jellyfin_plugin_toolkit import binding_pr


class GitHubApiTests(unittest.TestCase):
    def test_repository_metadata_uses_the_root_endpoint_without_a_trailing_slash(self):
        with patch.object(
            binding_pr.subprocess,
            "check_output",
            return_value=b'{"default_branch":"main"}',
        ) as request:
            self.assertEqual(
                {"default_branch": "main"}, binding_pr.api("example/plugin", "")
            )
        request.assert_called_once_with(
            ["gh", "api", "--method", "GET", "repos/example/plugin"], input=None
        )

    def test_nested_endpoint_retains_method_and_write_body(self):
        with patch.object(
            binding_pr.subprocess, "check_output", return_value=b"{}"
        ) as request:
            binding_pr.api("example/plugin", "pulls/17", "PATCH", {"state": "open"})
        request.assert_called_once_with(
            [
                "gh",
                "api",
                "--method",
                "PATCH",
                "repos/example/plugin/pulls/17",
                "--input",
                "-",
            ],
            input=b'{"state": "open"}',
        )


class BindingPRTests(unittest.TestCase):
    def fixture(self):
        repo = {"full_name": "example/plugin"}
        return {
            "state": "open",
            "number": 17,
            "user": {"login": "renovate[bot]", "type": "Bot"},
            "head": {"sha": "a" * 40, "repo": repo, "ref": "update"},
            "base": {"sha": "b" * 40, "repo": repo},
        }

    def test_only_same_repository_bot_prs_are_eligible(self):
        pr = self.fixture()
        self.assertTrue(binding_pr.eligible(pr, "example/plugin", "renovate[bot]"))
        for changed in [
            dict(pr, state="closed"),
            dict(pr, user={"login": "human", "type": "User"}),
        ]:
            self.assertFalse(
                binding_pr.eligible(changed, "example/plugin", "renovate[bot]")
            )
        fork = copy.deepcopy(pr)
        fork["head"]["repo"] = {"full_name": "fork/plugin"}
        self.assertFalse(binding_pr.eligible(fork, "example/plugin", "renovate[bot]"))

    def test_tampered_proposal_is_rejected_before_writing(self):
        proposal = {
            "schema": 1,
            "repository": "example/plugin",
            "number": 17,
            "changes": [{"evil": True}],
        }
        with (
            patch.object(binding_pr, "api", return_value=self.fixture()) as api,
            patch.object(
                binding_pr, "prepare", return_value=dict(proposal, changes=[])
            ),
        ):
            with self.assertRaises(ValueError):
                binding_pr.commit(None, "example/plugin", proposal, "renovate[bot]")
        self.assertTrue(all(len(call.args) < 3 for call in api.call_args_list))

    def test_identity_failure_stops_before_any_write(self):
        proposal = {
            "schema": 1,
            "repository": "example/plugin",
            "number": 17,
            "changes": [{"name": "uv.lock"}],
        }
        with (
            patch.object(binding_pr, "api", return_value=self.fixture()) as api,
            patch.object(binding_pr, "prepare", return_value=proposal),
            patch.object(
                binding_pr,
                "commit_identity",
                side_effect=ValueError("Invalid App identity"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "App identity"):
                binding_pr.commit(None, "example/plugin", proposal, "renovate[bot]")
        self.assertTrue(all(len(call.args) < 3 for call in api.call_args_list))

    def test_app_is_both_author_and_committer(self):
        pr = self.fixture()
        proposal = {
            "schema": 1,
            "repository": "example/plugin",
            "number": 17,
            "head": pr["head"]["sha"],
            "base": pr["base"]["sha"],
            "changes": [{"name": "uv.lock", "after": "", "mode": 0o644}],
        }
        identity = {
            "name": "fixture-app[bot]",
            "email": "123+fixture-app[bot]@users.noreply.github.com",
        }
        writes = []

        def api(repository, endpoint, method="GET", body=None):
            if method != "GET":
                writes.append((endpoint, body))
                return {"sha": "c" * 40}
            if endpoint.startswith("pulls/"):
                return pr
            return {"tree": {"sha": "d" * 40}}

        with (
            patch.object(binding_pr, "api", side_effect=api),
            patch.object(binding_pr, "prepare", return_value=proposal),
            patch.object(binding_pr, "commit_identity", return_value=identity),
            patch.object(binding_pr, "check_renovate"),
        ):
            binding_pr.commit(None, "example/plugin", proposal, "renovate[bot]")
        created = next(body for endpoint, body in writes if endpoint == "git/commits")
        self.assertEqual(identity, created["author"])
        self.assertEqual(identity, created["committer"])
        self.assertFalse(writes[-1][1]["force"])

    def test_invalid_proposal_is_rejected_without_github_access(self):
        with patch.object(binding_pr, "api") as api:
            with self.assertRaises(ValueError):
                binding_pr.commit(None, "example/plugin", {}, "renovate[bot]")
        api.assert_not_called()


class NativeResolverBoundaryTests(unittest.TestCase):
    def test_only_canonical_toolkit_source_can_change(self):
        before = b'[project]\nname="example"\ndependencies=["jellyfin-plugin-toolkit"]\n[tool.uv.sources]\njellyfin-plugin-toolkit={url="old"}\n'
        after = before.replace(b'url="old"', b'url="new"')
        binding_pr.validate_project_update(before, after)
        for injected in [
            after.replace(b"dependencies=[", b'dependencies=["malicious>=1",'),
            after
            + b'\n[build-system]\nrequires=["untrusted-backend"]\nbuild-backend="untrusted"\n',
            after + b'\n[tool.uv]\nindex-url="https://attacker.invalid"\n',
        ]:
            with self.subTest(injected=injected):
                with self.assertRaisesRegex(ValueError, "unrelated Python"):
                    binding_pr.validate_project_update(before, injected)
