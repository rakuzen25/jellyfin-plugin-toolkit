"""Published fixes, strict metadata, and narrowly scoped expiring waivers."""

import copy
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jellyfin_plugin_toolkit import advisories as audit
from jellyfin_plugin_toolkit.configuration import Advisories, AdvisoryWaiver

ID = "GHSA-vfj7-8cjw-p6xm"
NOW = datetime.now(UTC)


def waiver(**changes):
    return AdvisoryWaiver.model_validate(
        dict(
            ecosystem="npm",
            package="braces",
            version="3.0.3",
            ghsa=ID,
            reason="Major upgrade needs integration work",
            issue="https://github.com/example/plugin/issues/1",
            issued_at=(NOW - timedelta(days=1)).isoformat(),
            expires_at=(NOW + timedelta(days=7)).isoformat(),
            **changes,
        )
    )


def entry(fixes=None, severity="high", **changes):
    result = audit.finding(
        "npm",
        "braces",
        "3.0.3",
        ID,
        severity,
        ["remark-cli>braces"],
        [] if fixes is None else fixes,
    )
    result.update(changes)
    return result


class AdvisoryTests(unittest.TestCase):
    def test_fixes_block_at_every_severity_and_unavailable_fixes_warn(self):
        for severity in ("low", "moderate", "high", "critical"):
            for fixes in ([], [">=4.0.0"], ["4.0.0-beta.1"]):
                findings, _ = audit.evaluate([entry(fixes, severity)], [], NOW)
                self.assertEqual(bool(fixes), findings[0]["blocking"])

    def test_exact_scope_expiry_renewal_and_deduplication(self):
        selected = waiver()
        finding = entry([">=4.0.0"])
        findings, warnings = audit.evaluate(
            [finding, {**finding, "locations": ["proxy>braces"]}], [selected], NOW
        )
        self.assertFalse(findings[0]["blocking"])
        self.assertEqual(2, len(findings[0]["locations"]))
        self.assertEqual([], warnings)
        findings, _ = audit.evaluate(
            [finding], [selected], selected.timestamp(selected.expires_at)
        )
        self.assertTrue(findings[0]["blocking"])
        findings, _ = audit.evaluate([{**finding, "version": "3.0.2"}], [selected], NOW)
        self.assertTrue(findings[0]["blocking"])
        findings, warnings = audit.evaluate([], [selected], NOW + timedelta(days=10))
        self.assertEqual([], findings)
        self.assertIn("Expired", warnings[0])
        _, warnings = audit.evaluate([], [selected], NOW)
        self.assertIn("Unused", warnings[0])
        findings, _ = audit.evaluate([finding], [waiver()], NOW)
        self.assertFalse(findings[0]["blocking"])

    def test_invalid_waiver_dates_and_duplicate_case_insensitive_scopes(self):
        data = waiver().model_dump()
        for changes in (
            {"issued_at": (NOW + timedelta(days=1)).isoformat()},
            {"expires_at": (NOW + timedelta(days=31)).isoformat()},
            {"expires_at": data["issued_at"]},
            {"issued_at": "2026-01-01"},
            {"reason": " "},
            {"issue": "http://example.com/1"},
            {"expires_at": "2026-01-01T00:00:00+01:00"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                AdvisoryWaiver.model_validate({**data, **changes})
        a = AdvisoryWaiver.model_validate(
            {**data, "ecosystem": "nuget", "package": "Example"}
        )
        b = AdvisoryWaiver.model_validate({**a.model_dump(), "package": "example"})
        with self.assertRaises(ValueError):
            Advisories(waivers=[a, b])
        findings, _ = audit.evaluate(
            [entry(["4"], ecosystem="nuget", package="EXAMPLE")], [a], NOW
        )
        self.assertFalse(findings[0]["blocking"])

    def test_node_metadata_unpublished_missing_and_exclusions(self):
        report = {
            "metadata": {"vulnerabilities": {"critical": 1}},
            "advisories": {
                "1": {
                    "patched_versions": None,
                    "patched_versions_unpublished": True,
                    "module_name": "braces",
                    "github_advisory_id": ID,
                    "severity": "critical",
                    "findings": [{"version": "3.0.3", "paths": ["remark>braces"]}],
                }
            },
        }
        for patched, unpublished, expected in (
            (None, True, False),
            (">=4", False, True),
            (">=4", True, False),
        ):
            candidate = copy.deepcopy(report)
            candidate["advisories"]["1"].update(
                patched_versions=patched, patched_versions_unpublished=unpublished
            )
            with patch.object(audit, "command_json", side_effect=[{}, candidate]):
                findings = []
                audit.node_findings(None, findings)
            self.assertEqual(expected, bool(findings[0]["fixes"]))
        for config in (
            {"audit": {"ignore": [ID]}},
            {"auditConfig": {"ignoreGhsas": [ID]}},
            {"audit.ignore": [ID]},
        ):
            with (
                patch.object(audit, "command_json", return_value=config),
                self.assertRaisesRegex(ValueError, "exclusions"),
            ):
                audit.node_findings(None, [])
        for bad in (
            {},
            {"metadata": {}, "advisories": []},
            {"metadata": {}, "advisories": {"1": {}}},
        ):
            with (
                patch.object(audit, "command_json", side_effect=[{}, bad]),
                self.assertRaises((ValueError, KeyError)),
            ):
                audit.node_findings(None, [])

    def test_nuget_fixes_match_package_not_severity_or_constraints(self):
        data = {
            "vulnerabilities": [
                {
                    "package": {"ecosystem": "nuget", "name": "Example"},
                    "first_patched_version": None,
                }
            ]
        }
        self.assertEqual([], audit.nuget_fixes(data, "EXAMPLE"))
        data["vulnerabilities"][0]["first_patched_version"] = "9.0.0-preview.1"
        self.assertEqual(["9.0.0-preview.1"], audit.nuget_fixes(data, "example"))
        with self.assertRaises(ValueError):
            audit.nuget_fixes(data, "another")
        del data["vulnerabilities"][0]["first_patched_version"]
        with self.assertRaises(KeyError):
            audit.nuget_fixes(data, "example")

    def test_conflicting_fix_metadata_and_unknown_counts_fail(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            audit.evaluate([entry(), entry(["4.0.0"])], [], NOW)
        with (
            patch.object(
                audit,
                "command_json",
                side_effect=[
                    {},
                    {"metadata": {"vulnerabilities": {"high": 1}}, "advisories": {}},
                ],
            ),
            self.assertRaisesRegex(ValueError, "counts"),
        ):
            audit.node_findings(None, [])

    def test_native_nuget_suppressions_stop_before_restore(self):
        with (
            patch.object(
                audit,
                "command_json",
                return_value={"Items": {"NuGetAuditSuppress": [{}]}},
            ),
            patch.object(audit, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "suppressions"):
                audit.nuget_findings(None, "x.csproj", [], {})
        run.assert_not_called()

    def test_nuget_api_is_cached_across_versions_and_projects(self):
        report = {
            "version": 1,
            "projects": [
                {
                    "frameworks": [
                        {
                            "framework": "net10.0",
                            "transitivePackages": [
                                {
                                    "id": "Example",
                                    "resolvedVersion": "1.0.0",
                                    "vulnerabilities": [
                                        {
                                            "severity": "low",
                                            "advisoryurl": "https://github.com/advisories/"
                                            + ID,
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            ],
        }
        metadata = {
            "vulnerabilities": [
                {
                    "package": {"ecosystem": "nuget", "name": "example"},
                    "first_patched_version": None,
                }
            ]
        }
        with (
            patch.object(
                audit,
                "command_json",
                side_effect=[{"Items": {"NuGetAuditSuppress": []}}, report] * 2,
            ),
            patch.object(audit, "run"),
            patch.object(audit, "github_advisory", return_value=metadata) as remote,
        ):
            findings = []
            cache = {}
            for project in ("a.csproj", "b.csproj"):
                audit.nuget_findings(
                    SimpleNamespace(root=Path(".")), project, findings, cache
                )
        remote.assert_called_once_with(ID)
        result, _ = audit.evaluate(findings, [], NOW)
        self.assertEqual(2, len(result[0]["locations"]))
        with (
            patch.object(
                audit,
                "command_json",
                side_effect=[{"Items": {"NuGetAuditSuppress": []}}, report] * 2,
            ),
            patch.object(audit, "run"),
            patch.object(
                audit, "github_advisory", side_effect=OSError("offline")
            ) as remote,
        ):
            findings = []
            cache = {}
            for project in ("a.csproj", "b.csproj"):
                with self.assertRaisesRegex(ValueError, "metadata unavailable"):
                    audit.nuget_findings(
                        SimpleNamespace(root=Path(".")), project, findings, cache
                    )
        remote.assert_called_once_with(ID)
        self.assertEqual(2, len(findings))
        self.assertEqual("unknown", findings[0]["fix_status"])
        with self.assertRaisesRegex(ValueError, "Unknown fix status"):
            audit.evaluate(findings, [], NOW)

    def test_findings_warn_by_default_and_enforcement_is_opt_in(self):
        for enforce in (False, True):
            with (
                self.subTest(enforce=enforce),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "pnpm-lock.yaml").touch()
                summary = root / "summary.md"
                ctx = SimpleNamespace(
                    root=root,
                    config=SimpleNamespace(
                        backend=SimpleNamespace(projects={}, tests=[]),
                        advisories=Advisories(fail_on_findings=True)
                        if enforce
                        else Advisories(),
                    ),
                )

                def node(_ctx, findings):
                    findings.append(entry([">=4.0.0"]))

                with (
                    patch.object(audit, "node_findings", side_effect=node),
                    patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary)}),
                ):
                    if enforce:
                        with self.assertRaisesRegex(
                            ValueError, "Advisory audit failed"
                        ):
                            audit.audit(ctx)
                    else:
                        audit.audit(ctx)
                report = json.loads(
                    (root / "dist/evidence/advisories.json").read_text()
                )
                self.assertEqual(not enforce, report["success"])
                self.assertEqual(enforce, report["fail_on_findings"])
                self.assertEqual(enforce, report["findings"][0]["blocking"])
                self.assertEqual("published", report["findings"][0]["fix_status"])
                self.assertIn(ID, summary.read_text())
                self.assertIn("ERROR:" if enforce else "WARNING:", summary.read_text())

    def test_failures_keep_partial_findings_and_always_write_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pnpm-lock.yaml").touch()
            ctx = SimpleNamespace(
                root=root,
                config=SimpleNamespace(
                    backend=SimpleNamespace(projects={"Host": "a.csproj"}, tests=[]),
                    advisories=Advisories(),
                ),
            )

            def node(_ctx, findings):
                findings.append(entry())

            with (
                patch.object(audit, "node_findings", side_effect=node),
                patch.object(
                    audit, "nuget_findings", side_effect=ValueError("Audit unavailable")
                ),
            ):
                with self.assertRaises(ValueError):
                    audit.audit(ctx)
            report = json.loads((root / "dist/evidence/advisories.json").read_text())
            self.assertFalse(report["success"])
            self.assertEqual(1, len(report["findings"]))
            self.assertEqual(
                ["NuGet audit: a.csproj: Audit unavailable"], report["errors"]
            )

    def test_bad_exit_codes_and_malformed_output_fail_closed(self):
        ctx = SimpleNamespace(root=Path("."))
        for status, output in (
            (2, "{}"),
            (0, "no json"),
            (1, '{"error":"offline"}'),
            (0, "[]"),
        ):
            with (
                patch.object(
                    audit.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], status, output, ""),
                ),
                self.assertRaises(ValueError),
            ):
                audit.command_json(ctx, "pnpm", finding_exit=True)
