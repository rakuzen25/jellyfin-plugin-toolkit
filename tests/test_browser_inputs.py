"""Expensive fixture-only checks reuse results for identical emitted behavior."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from consumer_fixture import ConsumerTestCase

from jellyfin_plugin_toolkit.ci import browser, evidence, planning, policy
from jellyfin_plugin_toolkit.configuration import BrowserInputs, Rule


class BrowserInputTests(ConsumerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ctx.config.validation.rules.append(
            Rule(
                patterns=["package.json", "pnpm-lock.yaml", "tests/browser/**"],
                groups=["frontend", "performance"],
            )
        )
        self.ctx.config.validation.browser_inputs = {
            key: BrowserInputs.model_validate(value)
            for key, value in (
                {
                    "performance": {
                        "files": [
                            "tests/browser/performance/**",
                            "tests/browser/fixtures/**",
                            "tests/browser/playwright.config.cjs",
                        ],
                        "assets": ["renderer.js", "renderer.css"],
                        "projects": ["stress-chromium", "stress-mobile-chromium"],
                        "workers": 1,
                    }
                }
            ).items()
        }
        for name, content in {
            "tests/browser/performance/song.spec.cjs": "test playback",
            "tests/browser/fixtures/demo.cjs": "synthetic fixture",
            "tests/browser/playwright.config.cjs": "projects",
            "src/Host/Plugin.cs": "backend",
            "package.json": "{}",
            "pnpm-lock.yaml": "lint package lock",
            "dist/web/renderer.js": "render()",
            "dist/web/renderer.css": "color: red",
            "dist/web/config.html": "administrator page",
            ".github/workflows/validate.yml": "validation workflow",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def key(self) -> str:
        return policy.fingerprints(self.ctx, browser.capture(self.ctx))["performance"]

    def test_planning_needs_no_node_build_or_emitted_assets(self) -> None:
        import shutil

        shutil.rmtree(self.root / "dist/web")

        def git(_root: Path, *args: str) -> str:
            return "frontend/input.ts" if args[0] == "diff" else "a" * 40

        with (
            patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_REPOSITORY": "",
                    "GH_TOKEN": "",
                },
            ),
            patch.object(
                policy,
                "git",
                side_effect=git,
            ),
            patch(
                "jellyfin_plugin_toolkit.tasks.frontend_build",
                side_effect=AssertionError("Planning must remain Python-only"),
            ),
        ):
            result = planning.plan(self.ctx, "previous", False, False, False, False)
        self.assertEqual({}, result["browser"])
        self.assertIn("performance", result["execute"])

    def test_frontend_refinement_reuses_identical_emitted_bytes(self) -> None:
        selection: dict[str, Any] = {
            "required": ["performance"],
            "execute": ["performance"],
            "reuse_allowed": True,
            "force_execution": False,
            "reused": {},
            "repository": "example/plugin",
            "policy": "policy",
        }
        expected = self.key()
        with (
            patch.dict(os.environ, {"GH_TOKEN": "read-only-fixture"}),
            patch.object(
                evidence,
                "evidence",
                return_value={"performance": {"run_id": "123", "input": expected}},
            ),
        ):
            result = planning.refine(self.ctx, selection)
        self.assertEqual(expected, result["inputs"]["performance"])
        self.assertEqual([], result["execute"])
        self.assertEqual("123", result["reused"]["performance"]["run_id"])

    def test_unavailable_refined_evidence_runs_the_browser_check(self) -> None:
        selection: dict[str, Any] = {
            "required": ["performance"],
            "execute": ["performance"],
            "reuse_allowed": True,
            "force_execution": False,
            "reused": {},
            "repository": "example/plugin",
            "policy": "policy",
        }
        with (
            patch.dict(os.environ, {"GH_TOKEN": "read-only-fixture"}),
            patch.object(evidence, "evidence", side_effect=OSError("unavailable")),
        ):
            result = planning.refine(self.ctx, selection)
        self.assertEqual(["performance"], result["execute"])

    def test_python_toolchain_changes_invalidate_all_groups_and_old_browser_evidence(
        self,
    ) -> None:
        for name in (".python-version", "pyproject.toml"):
            with self.subTest(name=name):
                proof = browser.capture(self.ctx)
                original = policy.fingerprints(self.ctx, proof)
                path = self.root / name
                before = path.read_text()
                path.write_text(
                    before.replace("3.15.0", "3.15.1").replace("0.13.1", "0.13.5")
                )
                with self.assertRaisesRegex(ValueError, "execution inputs changed"):
                    browser.verify(self.ctx, proof)
                updated = policy.fingerprints(self.ctx, browser.capture(self.ctx))
                self.assertTrue(
                    all(original[group] != updated[group] for group in self.ctx.groups)
                )
                self.assertEqual(set(self.ctx.groups), policy.impacts(self.ctx, name))
                path.write_text(before)

    def test_backend_lint_and_unused_assets_do_not_invalidate_stress(self) -> None:
        original = self.key()
        before_policy = policy.policy_fingerprint(self.ctx)
        for name in ["src/Host/Plugin.cs", "pnpm-lock.yaml", "dist/web/config.html"]:
            with self.subTest(name=name):
                (self.root / name).write_text("changed")
                self.assertEqual(original, self.key())
                self.assertEqual(before_policy, policy.policy_fingerprint(self.ctx))

    def test_waiver_changes_affect_validation_inputs_but_not_stress_reuse(self) -> None:
        from datetime import UTC, datetime, timedelta

        from jellyfin_plugin_toolkit.configuration import AdvisoryWaiver
        from jellyfin_plugin_toolkit.scaffold import toml

        original = self.key()
        before_policy = policy.policy_fingerprint(self.ctx)
        before_artifacts = policy.fingerprints(self.ctx, browser.capture(self.ctx))[
            "artifacts"
        ]
        now = datetime.now(UTC)
        self.ctx.config.advisories.waivers.append(
            AdvisoryWaiver(
                ecosystem="npm",
                package="example",
                version="1.0.0",
                ghsa="GHSA-abcd-efgh-ijkl",
                reason="Upgrade integration",
                issue="https://github.com/example/plugin/issues/1",
                issued_at=(now - timedelta(days=1)).isoformat(),
                expires_at=(now + timedelta(days=7)).isoformat(),
            )
        )
        (self.root / "plugin.toml").write_text(toml(self.ctx.config.data()))
        self.assertEqual(original, self.key())
        self.assertEqual(before_policy, policy.policy_fingerprint(self.ctx))
        self.assertNotEqual(
            before_artifacts,
            policy.fingerprints(self.ctx, browser.capture(self.ctx))["artifacts"],
        )

    def test_python_dev_lock_changes_do_not_invalidate_stress_or_policy(self) -> None:
        original = self.key()
        before_policy = policy.policy_fingerprint(self.ctx)
        lock = self.root / "uv.lock"
        # A different development package changes the complete lock digest, while
        # the toolkit source/version/hash and consumed browser inputs stay fixed.
        lock.write_text(
            lock.read_text()
            + '\n[[package]]\nname="ruff"\nversion="0.17.2"\nsource={registry="https://pypi.org/simple"}\n'
        )
        self.assertEqual(original, self.key())
        self.assertEqual(before_policy, policy.policy_fingerprint(self.ctx))

    def test_behavior_fixtures_tests_and_execution_settings_invalidate_stress(
        self,
    ) -> None:
        for name in [
            "dist/web/renderer.js",
            "dist/web/renderer.css",
            "tests/browser/fixtures/demo.cjs",
            "tests/browser/performance/song.spec.cjs",
            "tests/browser/playwright.config.cjs",
        ]:
            with self.subTest(name=name):
                original = self.key()
                (self.root / name).write_text("changed")
                self.assertNotEqual(original, self.key())
        original = self.key()
        self.ctx.config.images["playwright"] = "image@sha256:" + "a" * 64
        self.assertNotEqual(original, self.key())
        original = self.key()
        self.ctx.config.validation.browser_inputs["performance"].workers = 2
        self.assertNotEqual(original, self.key())
        original = self.key()
        with patch.dict(os.environ, {"ImageVersion": "different-runner-image"}):
            self.assertNotEqual(original, self.key())

    def test_unclassified_and_missing_precise_inputs_fail_closed(self) -> None:
        self.ctx.config.validation.browser_inputs["performance"].files.append(
            "tests/missing/**"
        )
        with self.assertRaisesRegex(ValueError, "matches no maintained"):
            self.key()

    def test_publication_can_verify_provenance_without_rebuilding_assets(self) -> None:
        proof = browser.capture(self.ctx)
        expected = browser.verify(self.ctx, proof)
        for path in (self.root / "dist/web").iterdir():
            path.unlink()
        self.assertEqual(expected, browser.verify(self.ctx, proof))
        (self.root / "frontend/admin/config.ts").write_text("changed source")
        with self.assertRaisesRegex(ValueError, "source or execution"):
            browser.verify(self.ctx, proof)

    def test_execution_must_match_planned_assets_and_settings(self) -> None:
        selection: dict[str, Any] = {"browser": browser.capture(self.ctx)}
        browser.verify_execution(
            self.ctx, selection, "performance", "stress-chromium", 1
        )
        with self.assertRaisesRegex(ValueError, "projects/workers"):
            browser.verify_execution(
                self.ctx, selection, "performance", "stress-chromium", 2
            )
        (self.root / "dist/web/renderer.js").write_text("different")
        with self.assertRaisesRegex(ValueError, "differ from"):
            browser.verify_execution(
                self.ctx, selection, "performance", "stress-chromium", 1
            )

    def test_malformed_asset_receipts_are_rejected(self) -> None:
        proof = browser.capture(self.ctx)
        proof["performance"]["assets"] = {"../outside.js": "a" * 64}
        with self.assertRaises(ValueError):
            browser.verify(self.ctx, proof)

    def test_changing_group_contract_or_workflow_invalidates_reuse_policy(self) -> None:
        initial = policy.policy_fingerprint(self.ctx)
        (self.root / ".github/workflows/validate.yml").write_text("new workflow")
        self.assertNotEqual(initial, policy.policy_fingerprint(self.ctx))

    def test_reuse_receipt_records_direct_executions(self) -> None:
        # A direct reference may never point at another reused result.
        run: dict[str, Any] = {"id": 2}
        receipt: dict[str, Any] = {
            "policy": "p",
            "inputs": {"backend": "f"},
            "groups": {"backend": "success"},
            "reused": {"backend": {"run_id": "1"}},
        }
        origin: dict[str, Any] = {**receipt, "reused": {"backend": {"run_id": "0"}}}
        with (
            patch.object(
                evidence,
                "api",
                return_value={"id": 1, "path": ".github/workflows/validate.yml"},
            ),
            patch.object(evidence, "read_receipt", return_value=origin),
        ):
            self.assertIsNone(
                evidence.completed_group_origin(
                    self.ctx, "owner/repo", run, receipt, "backend", "p", "f", {}
                )
            )
