import copy
import unittest
from unittest.mock import patch

from jellyfin_plugin_toolkit import binding_pr


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
