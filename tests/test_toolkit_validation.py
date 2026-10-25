"""Release preparation accepts only the candidate actually covered by regular CI."""

import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jellyfin_plugin_toolkit import toolkit_validation as validation


class ToolkitValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = "example/toolkit"
        self.sha = "a" * 40
        self.workflow_run: dict[str, Any] = {
            "id": 123,
            "run_attempt": 2,
            "head_sha": self.sha,
            "head_branch": "main",
            "event": "push",
            "path": validation.WORKFLOW,
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": self.repository},
            "head_repository": {"full_name": self.repository},
        }
        self.jobs: dict[str, Any] = {
            "total_count": len(validation.JOBS),
            "jobs": [
                {"name": name, "conclusion": "success", "run_attempt": 2}
                for name in sorted(validation.JOBS)
            ],
        }
        self.artifacts: dict[str, Any] = {
            "total_count": 2,
            "artifacts": [
                {"id": 50, "name": "toolkit-validation-123-2", "expired": False},
                {"id": 40, "name": "toolkit-candidate-123-2", "expired": False},
            ],
        }
        self.receipt: dict[str, Any] = {
            "schema_version": 1,
            "repository": self.repository,
            "revision": self.sha,
            "run_id": 123,
            "run_attempt": 2,
            "candidate_artifact_id": 40,
            "results": dict.fromkeys(
                ("candidate", "toolkit", "starter", "advisories"), "success"
            ),
        }
        self.pending = False
        self.extra_member = False

    def api(self, repository: str, endpoint: str, binary: bool = False) -> object:
        self.assertEqual(self.repository, repository)
        if "/workflows/ci.yml/runs?" in endpoint:
            run = copy.deepcopy(self.workflow_run)
            if self.pending:
                self.pending = False
                run.update(status="in_progress", conclusion=None)
            return {"total_count": 1, "workflow_runs": [run]}
        if "/jobs?" in endpoint:
            return self.jobs
        if "/artifacts?" in endpoint:
            return self.artifacts
        if endpoint == "/actions/artifacts/50/zip":
            self.assertTrue(binary)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("validation.json", json.dumps(self.receipt))
                if self.extra_member:
                    archive.writestr("unexpected", "not a receipt")
            return payload.getvalue()
        self.fail("Unexpected endpoint: " + endpoint)

    def select(
        self, *, retry: bool = False, wait_seconds: int = 0
    ) -> validation.Selection | None:
        with patch.object(validation, "api", side_effect=self.api):
            return validation.select(
                self.repository, self.sha, retry=retry, wait_seconds=wait_seconds
            )

    def test_success_selects_exact_candidate(self) -> None:
        self.assertEqual(
            validation.Selection(run_id=123, artifact_id=40), self.select()
        )

    def test_release_preparation_waits_for_regular_ci(self) -> None:
        self.pending = True
        with patch("jellyfin_plugin_toolkit.toolkit_validation.time.sleep") as sleep:
            self.assertIsNotNone(self.select(wait_seconds=30))
        sleep.assert_called_once()

    def test_missing_run_never_starts_competing_automatic_validation(self) -> None:
        with patch.object(
            validation, "api", return_value={"total_count": 0, "workflow_runs": []}
        ):
            with self.assertRaisesRegex(ValueError, "Timed out"):
                validation.select(self.repository, self.sha, wait_seconds=0)
            self.assertIsNone(validation.select(self.repository, self.sha, retry=True))

    def test_failures_stop_even_manual_retry(self) -> None:
        for conclusion in ["failure", "timed_out", "cancelled"]:
            with self.subTest(conclusion=conclusion):
                self.workflow_run["conclusion"] = conclusion
                with self.assertRaisesRegex(ValueError, "ended with"):
                    self.select()
                if conclusion != "cancelled":
                    with self.assertRaises(ValueError):
                        self.select(retry=True)
                else:
                    self.assertIsNone(self.select(retry=True))

    def test_missing_or_expired_artifacts_allow_only_manual_fallback(self) -> None:
        for index in [0, 1]:
            with self.subTest(index=index):
                self.artifacts["artifacts"][index]["expired"] = True
                with self.assertRaisesRegex(ValueError, "missing or expired"):
                    self.select()
                self.assertIsNone(self.select(retry=True))
                self.artifacts["artifacts"][index]["expired"] = False

    def test_partial_rerun_binds_original_candidate_to_new_successful_receipt(
        self,
    ) -> None:
        for job in self.jobs["jobs"]:
            if job["name"] == "candidate":
                job["run_attempt"] = 1
        self.artifacts["artifacts"][1]["name"] = "toolkit-candidate-123-1"
        self.assertIsNotNone(self.select())
        self.receipt["candidate_artifact_id"] = 999
        self.assertIsNone(self.select(retry=True))

    def test_wrong_workflow_event_repository_branch_or_commit_is_ineligible(
        self,
    ) -> None:
        replacements = {
            "event": "pull_request",
            "path": ".github/workflows/untrusted.yml",
            "head_sha": "b" * 40,
            "head_branch": "feature",
            "repository": {"full_name": "attacker/repo"},
            "head_repository": {"full_name": "attacker/repo"},
        }
        for key, value in replacements.items():
            with self.subTest(key=key):
                run = dict(self.workflow_run, **{key: value})
                self.assertFalse(
                    validation.eligible(
                        validation.Run.model_validate(run), self.repository, self.sha
                    )
                )

    def test_receipt_and_job_tampering_fails_closed(self) -> None:
        for key, value in {
            "repository": "attacker/repo",
            "revision": "b" * 40,
            "run_attempt": 1,
            "run_id": 1,
            "schema_version": 2,
            "results": {"candidate": "success"},
        }.items():
            original = copy.deepcopy(self.receipt)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identity"):
                self.receipt[key] = value
                self.select(retry=True)
            self.receipt = original
        self.extra_member = True
        with self.assertRaisesRegex(ValueError, "archive"):
            self.select()
        self.extra_member = False
        self.jobs["jobs"][0]["conclusion"] = "skipped"
        with self.assertRaisesRegex(ValueError, "successful suite"):
            self.select()

    def test_older_success_without_a_receipt_can_be_revalidated_on_manual_retry(
        self,
    ) -> None:
        self.jobs = {"total_count": 0, "jobs": []}
        self.artifacts = {"total_count": 0, "artifacts": []}
        self.assertIsNone(self.select(retry=True))
        with self.assertRaisesRegex(ValueError, "missing or expired"):
            self.select()

    def test_wrong_candidate_attempt_is_rejected(self) -> None:
        self.artifacts["artifacts"][1]["name"] = "toolkit-candidate-123-1"
        with self.assertRaisesRegex(ValueError, "tested artifact"):
            self.select()

    def test_record_requires_every_validation_result(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            validation.record(
                directory,
                self.repository,
                self.sha,
                123,
                2,
                40,
                self.receipt["results"],
            )
            self.assertEqual(
                self.receipt, json.loads((directory / "validation.json").read_text())
            )
            for value in ["skipped", "failure", "cancelled"]:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    validation.record(
                        directory,
                        self.repository,
                        self.sha,
                        123,
                        2,
                        40,
                        dict(self.receipt["results"], starter=value),
                    )
