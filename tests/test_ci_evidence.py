"""Deterministic selection, receipt and clean-build safety regressions; no GitHub calls."""

import copy
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from consumer_fixture import ConsumerTestCase

import jellyfin_plugin_toolkit.ci.evidence as ci_evidence
import jellyfin_plugin_toolkit.ci.planning as ci_planning
import jellyfin_plugin_toolkit.ci.policy as ci_policy
from jellyfin_plugin_toolkit.context import Context


class IsolatedCiTestCase(ConsumerTestCase):
    def setUp(self):
        super().setUp()
        self.validation = ci_policy.ValidationPolicy.from_context(self.ctx)
        # Runner context must never select a different policy path or enable real
        # GitHub calls. Individual scenarios opt into push/release context below.
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GITHUB_")
            and key not in {"GH_TOKEN", "CI_RUN_HEAD_SHA", "CI_JOB_RESULTS"}
        }
        environment["GITHUB_EVENT_NAME"] = "pull_request"
        context = patch.dict(os.environ, environment, clear=True)
        context.start()
        self.addCleanup(context.stop)


class PolicyTests(IsolatedCiTestCase):
    def test_fork_or_unapproved_origin_is_rejected(self):
        run: dict = {
            "repository": {"full_name": "owner/repo"},
            "head_repository": {"full_name": "owner/repo"},
            "event": "push",
            "head_branch": "main",
        }
        self.assertTrue(ci_evidence.trusted(run, "owner/repo"))
        run["head_repository"]["full_name"] = "fork/repo"
        self.assertFalse(ci_evidence.trusted(run, "owner/repo"))

    def test_newest_matching_failure_prevents_old_green_reuse(self):
        runs = [
            {
                "id": 2,
                "path": ".github/workflows/validate.yml",
                "conclusion": "failure",
            },
            {
                "id": 1,
                "path": ".github/workflows/validate.yml",
                "conclusion": "success",
            },
        ]
        inputs = {g: g for g in self.validation.groups}

        def receipt(repo, run):
            return {
                "policy": "policy",
                "inputs": inputs,
                "groups": {
                    g: "failure" if run["id"] == 2 else "success"
                    for g in self.validation.code
                },
            }

        with (
            patch.object(ci_evidence, "api", return_value={"workflow_runs": runs}),
            patch.object(ci_evidence, "read_receipt", side_effect=receipt),
        ):
            self.assertEqual(
                ci_evidence.evidence(self.ctx, "owner/repo", inputs, "policy"), {}
            )

    def trusted_run(self) -> dict:
        return {
            "id": 100,
            "path": ".github/workflows/release.yml",
            "repository": {"full_name": "owner/repo"},
            "head_repository": {"full_name": "owner/repo"},
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": "workflow",
            "status": "completed",
            "conclusion": "success",
        }

    def test_pr_evidence_requires_same_repository_exact_head_and_merge_to_main(self):
        run = dict(
            self.trusted_run(),
            event="pull_request",
            path=".github/workflows/validate.yml",
            pull_requests=[{"number": 1}],
        )
        request: dict = {
            "merged": True,
            "head": {"repo": {"full_name": "owner/repo"}, "sha": "workflow"},
            "base": {"repo": {"full_name": "owner/repo"}, "ref": "main"},
        }
        self.assertTrue(ci_evidence.trusted(run, "owner/repo"))
        with patch.object(ci_evidence, "api", return_value=request):
            self.assertTrue(ci_evidence.merged_pull_request(run, "owner/repo"))
        for field in ("unmerged", "fork", "changed_head", "other_base"):
            altered = copy.deepcopy(request)
            if field == "unmerged":
                altered["merged"] = False
            if field == "fork":
                altered["head"]["repo"]["full_name"] = "fork/repo"
            if field == "changed_head":
                altered["head"]["sha"] = "changed"
            if field == "other_base":
                altered["base"]["ref"] = "development"
            with (
                self.subTest(field=field),
                patch.object(ci_evidence, "api", return_value=altered),
            ):
                self.assertFalse(ci_evidence.merged_pull_request(run, "owner/repo"))

    def test_expired_and_duplicate_receipt_artifacts_are_not_evidence(self):
        run = self.trusted_run()
        for artifacts in (
            [{"name": "plugin-validation-receipt", "expired": True}],
            [{"name": "plugin-validation-receipt", "expired": False}] * 2,
            [],
        ):
            with (
                self.subTest(artifacts=artifacts),
                patch.object(ci_evidence, "api", return_value={"artifacts": artifacts}),
            ):
                self.assertIsNone(ci_evidence.read_receipt("owner/repo", run))

    def test_artifact_redirect_does_not_disclose_actions_token(self):
        request = urllib.request.Request(
            "https://api.github.com/repos/owner/repo/actions/artifacts/1/zip",
            headers={"Authorization": "Bearer synthetic"},
        )
        redirected = ci_evidence.SafeRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://storage.example.invalid/signed"
        )
        self.assertIsNotNone(redirected)
        assert redirected is not None
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaises(ValueError):
            ci_evidence.SafeRedirect().redirect_request(
                request, None, 302, "Found", {}, "http://storage.example.invalid/signed"
            )

    def test_upload_retry_accepts_failed_attach_but_never_failed_validation(self):
        run = self.trusted_run()
        run["conclusion"] = "failure"  # attach can fail after complete validation
        receipt = {
            "run_head_sha": "workflow",
            "validated_at": datetime.now(UTC).isoformat(),
        }
        jobs = {
            "jobs": [
                {"name": "artifacts / validate / validate", "conclusion": "success"}
            ]
        }
        with patch.object(ci_evidence, "api", side_effect=[run, jobs]):
            ci_evidence.verify_run("owner/repo", "100", receipt)
        jobs["jobs"][0]["conclusion"] = "failure"
        with (
            patch.object(ci_evidence, "api", side_effect=[run, jobs]),
            self.assertRaisesRegex(ValueError, "aggregate"),
        ):
            ci_evidence.verify_run("owner/repo", "100", receipt)

    def test_invalid_run_id_is_rejected_before_external_requests(self):
        with (
            patch.object(ci_evidence, "api") as remote,
            self.assertRaisesRegex(ValueError, "numeric"),
        ):
            ci_evidence.verify_run("owner/repo", "../other", {})
        remote.assert_not_called()

    def test_old_release_receipt_requires_fresh_advisories(self):
        receipt = {
            "run_head_sha": "workflow",
            "validated_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        }
        with (
            patch.object(
                ci_evidence,
                "api",
                side_effect=[
                    self.trusted_run(),
                    {"jobs": [{"name": "validate", "conclusion": "success"}]},
                ],
            ),
            self.assertRaisesRegex(ValueError, "24 hours"),
        ):
            ci_evidence.verify_run("owner/repo", "100", receipt)


class GroupReuseTests(IsolatedCiTestCase):
    def fixture(self, conclusion="cancelled", status="completed"):
        run = dict(
            PolicyTests().trusted_run(),
            conclusion=conclusion,
            status=status,
            run_attempt=1,
        )
        receipt = {
            "policy": "policy",
            "inputs": {g: g for g in self.validation.code},
            "groups": {g: "success" for g in self.validation.code},
        }
        jobs = [
            {
                "name": "validate / " + name,
                "status": "completed",
                "conclusion": "success",
                "run_attempt": 1,
            }
            for names in self.validation.job_names.values()
            for name in names
        ]
        return run, receipt, jobs

    def test_completed_groups_reuse_independent_of_parent_outcome(self):
        for conclusion, status in (
            ("success", "completed"),
            ("failure", "completed"),
            ("cancelled", "completed"),
            ("timed_out", "completed"),
            ("neutral", "completed"),
            (None, "in_progress"),
        ):
            run, receipt, jobs = self.fixture(conclusion, status)
            with (
                self.subTest(conclusion=conclusion),
                patch.object(ci_evidence, "recent_runs", return_value=[run]),
                patch.object(ci_evidence, "read_receipt", return_value=receipt),
                patch.object(ci_evidence, "api", return_value={"jobs": jobs}),
            ):
                proofs = ci_evidence.evidence(
                    self.ctx, "owner/repo", receipt["inputs"], "policy"
                )
                self.assertEqual(set(proofs), set(self.validation.code))

    def test_unfinished_failed_and_wrong_attempt_jobs_are_not_reused(self):
        for state, conclusion, attempt in (
            ("in_progress", None, 1),
            ("completed", "cancelled", 1),
            ("completed", "failure", 1),
            ("completed", "skipped", 1),
            ("completed", "success", 2),
        ):
            run, receipt, jobs = self.fixture()
            backend = next(job for job in jobs if job["name"] == "validate / backend")
            backend.update(status=state, conclusion=conclusion, run_attempt=attempt)
            with (
                self.subTest(conclusion=conclusion, attempt=attempt),
                patch.object(ci_evidence, "recent_runs", return_value=[run]),
                patch.object(ci_evidence, "read_receipt", return_value=receipt),
                patch.object(ci_evidence, "api", return_value={"jobs": jobs}),
            ):
                proofs = ci_evidence.evidence(
                    self.ctx, "owner/repo", receipt["inputs"], "policy"
                )
                self.assertNotIn("backend", proofs)
                self.assertIn("frontend", proofs)

    def test_matrix_group_requires_every_expected_job(self):
        run, receipt, jobs = self.fixture()
        jobs = [job for job in jobs if job["name"] != "validate / rendering (webkit)"]
        with patch.object(ci_evidence, "api", return_value={"jobs": jobs}):
            self.assertIsNone(
                ci_evidence.completed_group_origin(
                    self.ctx,
                    "owner/repo",
                    run,
                    receipt,
                    "rendering",
                    "policy",
                    "rendering",
                    {},
                )
            )

    def test_direct_reference_resolves_to_original_job_and_rejects_cycles(self):
        run, receipt, jobs = self.fixture()
        origin = dict(run, id=99)
        receipt["reused"] = {"backend": {"run_id": "99"}}
        original = dict(receipt, reused={})
        with (
            patch.object(
                ci_evidence,
                "api",
                side_effect=lambda repo, endpoint: (
                    origin if endpoint == "/actions/runs/99" else {"jobs": jobs}
                ),
            ),
            patch.object(ci_evidence, "read_receipt", return_value=original),
        ):
            self.assertEqual(
                ci_evidence.completed_group_origin(
                    self.ctx,
                    "owner/repo",
                    run,
                    receipt,
                    "backend",
                    "policy",
                    "backend",
                    {},
                ),
                "99",
            )
            original["reused"] = {"backend": {"run_id": "99"}}
            self.assertIsNone(
                ci_evidence.completed_group_origin(
                    self.ctx,
                    "owner/repo",
                    run,
                    receipt,
                    "backend",
                    "policy",
                    "backend",
                    {},
                )
            )

    def test_expired_missing_or_mismatched_origin_proof_cannot_reuse(self):
        run, receipt, _jobs = self.fixture()
        receipt["reused"] = {"backend": {"run_id": "99"}}
        for proof in (
            None,
            dict(receipt, policy="changed"),
            dict(receipt, inputs={"backend": "different"}),
            dict(receipt, groups={"backend": "failure"}),
        ):
            with (
                self.subTest(proof=proof),
                patch.object(ci_evidence, "api", return_value=dict(run, id=99)),
                patch.object(ci_evidence, "read_receipt", return_value=proof),
            ):
                self.assertIsNone(
                    ci_evidence.completed_group_origin(
                        self.ctx,
                        "owner/repo",
                        run,
                        receipt,
                        "backend",
                        "policy",
                        "backend",
                        {},
                    )
                )

    def test_rerun_cannot_use_previous_attempt_receipt(self):
        import io
        import zipfile

        run, receipt, _jobs = self.fixture()
        run["run_attempt"] = 2
        receipt.update(
            schema=ci_policy.SCHEMA,
            run_id="100",
            repository="owner/repo",
            run_head_sha="workflow",
            required=list(self.validation.code),
            run_attempt=1,
        )
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("validation.json", json.dumps(receipt))
        with patch.object(
            ci_evidence,
            "api",
            side_effect=[
                {
                    "artifacts": [
                        {
                            "name": "plugin-validation-receipt",
                            "expired": False,
                            "id": 1,
                        }
                    ]
                },
                data.getvalue(),
            ],
        ):
            self.assertIsNone(ci_evidence.read_receipt("owner/repo", run))

    def test_in_progress_parent_can_supply_an_authenticated_receipt(self):
        import io
        import zipfile

        run, receipt, _jobs = self.fixture(None, "in_progress")
        receipt.update(
            schema=ci_policy.SCHEMA,
            run_id="100",
            repository="owner/repo",
            run_head_sha="workflow",
            required=list(self.validation.code),
            run_attempt=1,
        )
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("validation.json", json.dumps(receipt))
        with patch.object(
            ci_evidence,
            "api",
            side_effect=[
                {
                    "artifacts": [
                        {
                            "name": "plugin-validation-receipt",
                            "expired": False,
                            "id": 1,
                        }
                    ]
                },
                data.getvalue(),
            ],
        ):
            self.assertIsNotNone(ci_evidence.read_receipt("owner/repo", run))


class CumulativeCoverageTests(IsolatedCiTestCase):
    def test_real_git_ancestry_rejects_divergent_force_push_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def command(*args):
                return ci_policy.git(root, "-c", "commit.gpgsign=false", *args)

            command("init", "--quiet")
            command(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "base",
            )
            ancestor = command("rev-parse", "HEAD")
            command(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "old tip",
            )
            old_tip = command("rev-parse", "HEAD")
            command("checkout", "--quiet", "-b", "replacement", ancestor)
            command(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "new tip",
            )
            receipt = self.receipt_fixture()
            receipt["commit"] = old_tip
            with (
                patch.object(
                    ci_evidence, "recent_runs", return_value=[self.run_fixture()]
                ),
                patch.object(ci_evidence, "read_receipt", return_value=receipt),
            ):
                self.assertEqual(
                    ci_evidence.validated_ancestor(
                        Context(root, self.ctx.config), "owner/repo", "main", "policy"
                    ),
                    {},
                )
                receipt["commit"] = ancestor
                self.assertEqual(
                    ci_evidence.validated_ancestor(
                        Context(root, self.ctx.config), "owner/repo", "main", "policy"
                    )["commit"],
                    ancestor,
                )

    def test_expired_artifact_is_not_downloaded(self):
        run = PolicyTests().trusted_run()
        with patch.object(
            ci_evidence,
            "api",
            return_value={
                "artifacts": [{"name": "plugin-validation-receipt", "expired": True}]
            },
        ) as api:
            self.assertIsNone(ci_evidence.read_receipt("owner/repo", run))
            self.assertEqual(api.call_count, 1)

    def test_unknown_failed_run_without_receipt_blocks_older_reuse(self):
        run = PolicyTests().trusted_run()
        run["conclusion"] = "failure"
        with (
            patch.object(ci_evidence, "recent_runs", return_value=[run]),
            patch.object(ci_evidence, "read_receipt", return_value=None),
        ):
            self.assertEqual(
                ci_evidence.evidence(
                    self.ctx,
                    "owner/repo",
                    {g: g for g in self.validation.groups},
                    "policy",
                ),
                {},
            )

    def run_fixture(self, identifier=100, conclusion="success", branch="main"):
        return {
            "id": identifier,
            "head_branch": branch,
            "conclusion": conclusion,
            "path": ".github/workflows/release-please.yml",
        }

    def receipt_fixture(self, schema=ci_policy.SCHEMA, full=True):
        receipt = {
            "schema": schema,
            "commit": "a" * 40,
            "tree": "tree",
            "policy": "policy",
            "inputs": {g: g for g in self.validation.groups},
            "full": full,
            "required": list(self.validation.groups) if full else [],
            "groups": {g: "success" for g in self.validation.groups} if full else {},
        }
        return receipt

    def test_only_successful_same_branch_ancestors_supply_baselines(self):
        runs = [
            self.run_fixture(104, "cancelled"),
            self.run_fixture(103, "failure"),
            self.run_fixture(102, branch="other"),
            self.run_fixture(101),
            self.run_fixture(),
        ]

        def ancestry(root, *args):
            if args[2] == "b" * 40:
                raise subprocess.CalledProcessError(1, "git")
            return ""

        def receipt(repo, run):
            value = self.receipt_fixture()
            if run["id"] == 101:
                value["commit"] = "b" * 40
            return value

        with (
            patch.object(ci_evidence, "recent_runs", return_value=runs),
            patch.object(ci_evidence, "read_receipt", side_effect=receipt) as read,
            patch.object(ci_policy, "git", side_effect=ancestry),
        ):
            self.assertEqual(
                ci_evidence.validated_ancestor(
                    self.ctx, "owner/repo", "main", "policy"
                )["run_id"],
                "100",
            )
            self.assertEqual(read.call_count, 2)

    def test_unsupported_schemas_are_rejected_for_full_and_narrow_receipts(self):
        for full in (False, True):
            with (
                self.subTest(full=full),
                patch.object(
                    ci_evidence, "recent_runs", return_value=[self.run_fixture()]
                ),
                patch.object(
                    ci_evidence,
                    "read_receipt",
                    return_value=self.receipt_fixture(1, full),
                ),
                patch.object(ci_policy, "git", return_value=""),
            ):
                self.assertEqual(
                    bool(
                        ci_evidence.validated_ancestor(
                            self.ctx, "owner/repo", "main", "policy"
                        )
                    ),
                    False,
                )

    def test_cumulative_identity_and_policy_must_match(self):
        receipt = self.receipt_fixture(full=False)
        receipt["coverage"] = {
            "complete": True,
            "identity": ci_evidence.coverage_identity(receipt),
        }
        for invalid in (None, "identity", "policy", "failed"):
            value = copy.deepcopy(receipt)
            if invalid == "identity":
                value["coverage"]["identity"] = "invalid"
            if invalid == "policy":
                value["policy"] = "old"
            if invalid == "failed":
                value["required"] = ["backend"]
                value["groups"] = {"backend": "failure"}
            with (
                self.subTest(invalid=invalid),
                patch.object(
                    ci_evidence, "recent_runs", return_value=[self.run_fixture()]
                ),
                patch.object(ci_evidence, "read_receipt", return_value=value),
                patch.object(ci_policy, "git", return_value=""),
            ):
                self.assertEqual(
                    bool(
                        ci_evidence.validated_ancestor(
                            self.ctx, "owner/repo", "main", "policy"
                        )
                    ),
                    invalid is None,
                )

    def test_missing_or_expired_receipt_cannot_establish_baseline(self):
        with (
            patch.object(ci_evidence, "recent_runs", return_value=[self.run_fixture()]),
            patch.object(ci_evidence, "read_receipt", return_value=None),
        ):
            self.assertEqual(
                ci_evidence.validated_ancestor(
                    self.ctx, "owner/repo", "main", "policy"
                ),
                {},
            )

    def test_lookup_is_bounded_to_three_pages(self):
        with patch.object(
            ci_evidence,
            "api",
            return_value={"workflow_runs": [self.run_fixture()] * 100},
        ) as api:
            self.assertEqual(len(list(ci_evidence.recent_runs("owner/repo"))), 300)
            self.assertEqual(api.call_count, 3)

    def selected(
        self, changes, baseline=True, proofs=None, unavailable=False, event="push"
    ):
        inputs = {g: g for g in self.validation.groups}
        ancestor = (
            {
                "commit": "a" * 40,
                "run_id": "100",
                "identity": "coverage",
                "inputs": inputs,
            }
            if baseline
            else {}
        )

        def git(root, *args):
            if args[0] == "diff":
                self.assertEqual(
                    args[-2], "a" * 40 if event == "push" and baseline else "previous"
                )
                return "\n".join(changes)
            return "commit"

        with (
            patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_NAME": event,
                    "GITHUB_REF_NAME": "main",
                    "GITHUB_REPOSITORY": "owner/repo",
                    "GH_TOKEN": "synthetic",
                },
            ),
            patch.object(
                ci_evidence,
                "validated_ancestor",
                side_effect=urllib.error.URLError("offline") if unavailable else None,
                return_value=ancestor,
            ),
            patch.object(ci_policy, "fingerprints", return_value=inputs),
            patch.object(ci_evidence, "evidence", return_value=proofs or {}),
            patch.object(ci_policy, "git", side_effect=git),
        ):
            return ci_planning.plan(self.ctx, "previous", False, False, True, False)

    def test_cancelled_renderer_changes_survive_later_config_or_docs_pushes(self):
        for last in (
            ".editorconfig",
            "README.md",
            "frontend/bridge/search/search-session.ts",
        ):
            result = self.selected(["frontend/renderer/playback/timeline.ts", last])
            self.assertIn("performance", result["execute"])
            self.assertIn("rendering", result["execute"])
            self.assertEqual(result["comparison"]["baseline"]["run_id"], "100")

    def test_failed_backend_and_multiple_cancelled_changes_are_cumulative(self):
        result = self.selected(
            [
                "src/Core/Lyrics/Models.cs",
                "frontend/renderer/playback/timeline.ts",
                "frontend/bridge/search/search-session.ts",
            ]
        )
        self.assertTrue(
            {"backend", "workflow", "performance"} <= set(result["execute"])
        )

    def test_documentation_after_successful_baseline_skips_checks(self):
        self.assertEqual(
            self.selected(["README.md", "docs/how-to/configure.md", "docs/SETUP.md"])[
                "execute"
            ],
            [],
        )

    def test_missing_baseline_or_api_requires_all_but_reuses_passed_groups(self):
        for unavailable in (False, True):
            result = self.selected(
                [],
                baseline=False,
                unavailable=unavailable,
                proofs={g: {"run_id": "100", "input": g} for g in self.validation.code},
            )
            self.assertEqual(set(result["required"]), set(self.validation.groups))
            self.assertEqual(result["execute"], ["artifacts"])
            self.assertTrue(result["comparison"]["fallback_reason"])

    def test_pr_still_uses_cumulative_merge_base(self):
        result = self.selected(
            ["frontend/renderer/playback/timeline.ts"], event="pull_request"
        )
        self.assertEqual(result["comparison"]["base"], "previous")
        self.assertIn("performance", result["execute"])

    def test_manual_full_validation_bypasses_matching_scheduled_evidence(self):
        inputs = {group: group for group in self.validation.groups}
        with (
            patch.dict(
                os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "synthetic"}
            ),
            patch.object(ci_policy, "fingerprints", return_value=inputs),
            patch.object(
                ci_evidence,
                "evidence",
                return_value={
                    group: {"run_id": "100", "input": group}
                    for group in self.validation.groups
                },
            ),
            patch.object(ci_policy, "git", return_value="commit"),
        ):
            selection = ci_planning.plan(self.ctx, "", True, False, True, True)
        self.assertEqual(set(selection["execute"]), set(self.validation.groups))
        self.assertEqual(selection["reused"], {})
        self.assertEqual(selection["full_baseline"], {})

    def test_shared_fingerprint_change_requires_group_even_without_path_selection(self):
        original = ci_policy.fingerprints(self.ctx)
        baseline = {"commit": "a" * 40, "inputs": dict(original, backend="older")}
        with (
            patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF_NAME": "main",
                    "GITHUB_REPOSITORY": "owner/repo",
                    "GH_TOKEN": "synthetic",
                },
            ),
            patch.object(ci_evidence, "validated_ancestor", return_value=baseline),
            patch.object(ci_evidence, "evidence", return_value={}),
            patch.object(
                ci_policy,
                "git",
                side_effect=lambda root, *args: (
                    "README.md" if args[0] == "diff" else "commit"
                ),
            ),
        ):
            self.assertIn(
                "backend",
                ci_planning.plan(self.ctx, "previous", False, False, True, False)[
                    "execute"
                ],
            )

    def test_failed_run_passed_groups_reused_but_matching_failures_block(self):
        inputs = {g: g for g in self.validation.groups}
        runs = [self.run_fixture(101, "failure"), self.run_fixture()]

        def receipt(repo, run):
            return {
                "policy": "policy",
                "inputs": inputs,
                "groups": {
                    g: "failure" if run["id"] == 101 and g == "backend" else "success"
                    for g in self.validation.code
                },
            }

        with (
            patch.object(ci_evidence, "recent_runs", return_value=runs),
            patch.object(ci_evidence, "read_receipt", side_effect=receipt),
            patch.object(
                ci_evidence,
                "api",
                return_value={
                    "jobs": [
                        {"name": name, "status": "completed", "conclusion": "success"}
                        for names in self.validation.job_names.values()
                        for name in names
                    ]
                },
            ),
        ):
            proofs = ci_evidence.evidence(self.ctx, "owner/repo", inputs, "policy")
        self.assertNotIn("backend", proofs)
        self.assertEqual(proofs["frontend"]["run_id"], "101")

    def test_success_extends_coverage_failure_does_not(self):
        selection = self.selected(["README.md"])
        self.assertTrue(
            ci_planning.finish(self.ctx, selection, {})["coverage"]["complete"]
        )
        selection = self.selected(["src/Core/Lyrics/Models.cs"])
        self.assertFalse(
            ci_planning.finish(self.ctx, selection, {})["coverage"]["complete"]
        )
