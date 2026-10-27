"""Public publication is an authenticated export, independent of CI schema changes."""

import hashlib
import json
import os
from typing import Any
from unittest.mock import patch

from consumer_fixture import ConsumerTestCase

from jellyfin_plugin_toolkit.ci import evidence, execution, policy
from jellyfin_plugin_toolkit.release.receipt import PublicReceipt


class PublicReceiptTests(ConsumerTestCase):
    def setUp(self) -> None:
        super().setUp()
        directory = self.root / "dist"
        directory.mkdir(exist_ok=True)
        artifact = directory / "example-0.1.0.zip"
        artifact.write_bytes(b"exact validated binary")
        self.receipt: dict[str, Any] = {
            "schema": policy.SCHEMA,
            "repository": "owner/repo",
            "commit": "a" * 40,
            "run_id": "42",
            "run_attempt": 1,
            "run_head_sha": "b" * 40,
            "release": True,
            "groups": {"metadata": "success", "artifacts": "success"},
            "artifacts": {
                artifact.name: hashlib.sha256(artifact.read_bytes()).hexdigest()
            },
            "toolkit": {
                "distribution": "jellyfin-plugin-toolkit",
                "version": "0.6.2",
                "source": "https://example.invalid/toolkit.whl",
                "sha256": "c" * 64,
                "python_lock_sha256": "d" * 64,
            },
            "validated_at": "2026-10-27T00:00:00+00:00",
            "policy": "private execution policy",
            "execute": ["metadata", "artifacts"],
        }
        self.private = directory / "validation.json"
        self.private.write_text(json.dumps(self.receipt))

    def test_export_authenticates_once_and_preserves_private_evidence_and_assets(
        self,
    ) -> None:
        original = self.private.read_bytes()
        output = self.root / "dist/release"
        calls: list[str] = []

        def authenticate(*args: object) -> None:
            self.assertFalse(output.exists())
            calls.append("authenticated")

        with (
            patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"),
            patch.object(evidence, "verify_run", side_effect=authenticate) as run,
            patch.object(evidence, "verify", side_effect=authenticate) as source,
        ):
            execution.release_receipt(self.ctx, "42")
        run.assert_called_once_with("owner/repo", "42", self.receipt)
        source.assert_called_once_with(self.ctx, self.receipt, "owner/repo", "42")
        self.assertEqual(2, len(calls))
        self.assertEqual(original, self.private.read_bytes())
        public = json.loads((output / "validation.json").read_text())
        self.assertEqual(7, public["schema"])
        self.assertNotEqual(policy.SCHEMA, public["schema"])
        self.assertNotIn("policy", public)
        self.assertNotIn("execute", public)
        for name in (
            "repository",
            "commit",
            "run_id",
            "run_attempt",
            "run_head_sha",
            "release",
            "groups",
            "artifacts",
            "toolkit",
            "validated_at",
        ):
            self.assertEqual(self.receipt[name], public[name])
        for name in public["artifacts"]:
            self.assertEqual(
                (self.root / "dist" / name).read_bytes(), (output / name).read_bytes()
            )

    def test_authentication_failure_cannot_write_a_public_receipt(self) -> None:
        for boundary in ("verify_run", "verify"):
            with (
                self.subTest(boundary=boundary),
                patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"),
                patch.object(evidence, "verify_run"),
                patch.object(evidence, "verify"),
                patch.object(evidence, boundary, side_effect=ValueError("untrusted")),
                self.assertRaisesRegex(ValueError, "untrusted"),
            ):
                execution.release_receipt(self.ctx, "42")
            self.assertFalse((self.root / "dist/release").exists())

    def test_public_contract_rejects_internal_schema_and_failed_gates(self) -> None:
        public = {
            key: value
            for key, value in self.receipt.items()
            if key not in {"policy", "execute"}
        }
        with self.assertRaises(ValueError):
            PublicReceipt.model_validate(public)
        public["schema"] = 7
        PublicReceipt.model_validate(public)
        public["groups"] = {"artifacts": "failure"}
        with self.assertRaises(ValueError):
            PublicReceipt.model_validate(public)
