"""Shared owners preserve coverage while avoiding unnecessary environments and audits."""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from consumer_fixture import ConsumerTestCase

from jellyfin_plugin_toolkit import audit_policy, binding_pr, quality, validators
from jellyfin_plugin_toolkit.ci import planning, policy
from jellyfin_plugin_toolkit.configuration import Configuration, Rule


class AuditSelectionTests(unittest.TestCase):
    def test_changed_dependency_inputs_and_freshness_require_audit(self) -> None:
        for path in (
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            "pnpm-lock.yaml",
            "frontend/package.json",
            "src/Host/Plugin.csproj",
            "nuget.config",
            "tests/backend/Host/packages.lock.json",
            "Directory.Packages.props",
            "plugin.toml",
            ".github/workflows/validate.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    audit_policy.required([path], fresh=False, unknown=False)
                )
        self.assertFalse(
            audit_policy.required(
                ["README.md", "src/Core/Policy.cs"], fresh=False, unknown=False
            )
        )
        self.assertTrue(audit_policy.required([], fresh=True, unknown=False))
        self.assertTrue(audit_policy.required([], fresh=False, unknown=True))


class ValidatorOwnershipTests(unittest.TestCase):
    def test_no_standalone_override_fields_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for field in (
                'images={actionlint="local"}',
                'toolchain_file="toolchain.json"',
            ):
                (root / "pyproject.toml").write_text(
                    '[tool.jpt.quality]\nchecks=["workflows"]\n' + field
                )
                with self.assertRaises(ValueError):
                    quality.selected(root)
            (root / "toolchain.json").write_text('{"gh":"2.0.0","renovate":"local"}')
            with self.assertRaisesRegex(ValueError, "toolkit-owned"):
                validators.reject_overrides(root)

    def test_repository_validator_uses_owned_pin_and_repository_mode_once(self) -> None:
        with patch.object(quality, "run") as execute:
            quality.automation(Path("/repository"), "renovate")
        execute.assert_called_once()
        self.assertIn(validators.image("renovate"), execute.call_args.args)
        self.assertIn("--no-global", execute.call_args.args)
        self.assertIn("--network", execute.call_args.args)


class SharedImpactTests(ConsumerTestCase):
    def test_standard_rules_work_without_copied_consumer_rules(self) -> None:
        self.ctx.config.validation.rules = []
        self.assertEqual(
            set(self.ctx.groups), policy.impacts(self.ctx, ".python-version")
        )
        self.assertIn("backend", policy.impacts(self.ctx, "src/Core/Policy.cs"))
        self.assertEqual({"metadata"}, policy.impacts(self.ctx, "renovate.json"))
        with self.assertRaisesRegex(ValueError, "Unclassified"):
            policy.impacts(self.ctx, "unknown/product.data")

    def test_local_extensions_cannot_remove_standard_obligations(self) -> None:
        self.ctx.config.validation.rules = [
            Rule(patterns=[".python-version"], groups=[]),
            Rule(patterns=["product/**"], groups=["backend"]),
        ]
        self.assertEqual(
            set(self.ctx.groups), policy.impacts(self.ctx, ".python-version")
        )
        self.assertEqual({"backend"}, policy.impacts(self.ctx, "product/native.data"))

    def test_plugin_validator_overrides_fail_at_configuration_boundary(self) -> None:
        config = self.ctx.config.data()
        config["images"]["actionlint"] = "local"
        with self.assertRaisesRegex(ValueError, "toolkit-owned"):
            Configuration.model_validate(config)

    def test_unrequired_audit_is_not_recorded_as_success(self) -> None:
        selection: dict[str, Any] = {
            "required": [],
            "execute": [],
            "reused": {},
            "audit_required": False,
        }
        receipt = planning.finish(
            self.ctx, selection, {"quality": {"result": "success"}}
        )
        self.assertEqual("not_required", receipt["advisories"]["result"])
        selection["audit_required"] = True
        receipt = planning.finish(
            self.ctx, selection, {"quality": {"result": "success"}}
        )
        self.assertEqual("missing", receipt["advisories"]["result"])


class SynchronizationRoutingTests(unittest.TestCase):
    def test_unrelated_python_change_is_noop_before_stale_base_guard(self) -> None:
        pr = {
            "state": "open",
            "user": {"login": "renovate[bot]", "type": "Bot"},
            "base": {"sha": "a" * 40, "repo": {"full_name": "example/plugin"}},
            "head": {"sha": "b" * 40, "repo": {"full_name": "example/plugin"}},
        }
        source = '[tool.uv.sources]\njellyfin-plugin-toolkit={url="canonical"}\n'
        with (
            patch("jellyfin_plugin_toolkit.binding_pr.subprocess.check_output"),
            patch(
                "jellyfin_plugin_toolkit.bindings.git",
                side_effect=[
                    source.encode(),
                    (source + '[tool.uv]\nrequired-version="==0.13.5"\n').encode(),
                ],
            ) as git,
        ):
            self.assertIsNone(
                binding_pr.prepare(Path("."), "example/plugin", pr, "renovate[bot]")
            )
        self.assertEqual(2, git.call_count)

    def test_metadata_route_skips_unrelated_closed_fork_and_superseded_prs(
        self,
    ) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/sync-bindings.yml"
        ).read_text()
        program = workflow.split("          node --input-type=module <<'JS'\n", 1)[
            1
        ].split("          JS\n", 1)[0]
        program = "\n".join(line[10:] for line in program.splitlines())
        project = '[tool.uv.sources]\njellyfin-plugin-toolkit={url="https://github.com/rakuzen25/jellyfin-plugin-toolkit/releases/download/v0.5.3/jellyfin_plugin_toolkit-0.5.3-py3-none-any.whl"}\n'
        import base64

        pr: dict[str, Any] = {
            "state": "open",
            "user": {"login": "renovate[bot]", "type": "Bot"},
            "base": {
                "sha": "a" * 40,
                "ref": "main",
                "repo": {"full_name": "example/plugin"},
            },
            "head": {"sha": "b" * 40, "repo": {"full_name": "example/plugin"}},
        }
        for scenario in (
            "unrelated",
            "toolkit",
            "closed",
            "fork",
            "superseded",
            "failed-validation",
            "metadata-only",
        ):
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as folder,
            ):
                root = Path(folder)
                selected = copy.deepcopy(pr)
                if scenario == "closed":
                    selected["state"] = "closed"
                if scenario == "fork":
                    selected["head"]["repo"]["full_name"] = "fork/plugin"
                head = (
                    project.replace("0.5.3", "0.5.4")
                    if scenario in {"toolkit", "failed-validation"}
                    else project
                )
                responses = {
                    "actions/runs/1/jobs?filter=latest&per_page=100": {
                        "total_count": 1,
                        "jobs": [
                            {
                                "name": "metadata-event"
                                if scenario == "metadata-only"
                                else "validation / validate"
                            }
                        ],
                    },
                    "actions/runs/1": {
                        "event": "pull_request",
                        "status": "completed",
                        "conclusion": "failure"
                        if scenario == "failed-validation"
                        else "success",
                        "path": ".github/workflows/validate.yml",
                        "head_repository": {"full_name": "example/plugin"},
                        "head_sha": "c" * 40 if scenario == "superseded" else "b" * 40,
                    },
                    "commits/"
                    + ("c" if scenario == "superseded" else "b") * 40
                    + "/pulls": [selected],
                    "contents/pyproject.toml?ref=" + "a" * 40: {
                        "content": base64.b64encode(project.encode()).decode()
                    },
                    "contents/pyproject.toml?ref=" + "b" * 40: {
                        "content": base64.b64encode(head.encode()).decode()
                    },
                }
                (root / "responses.json").write_text(json.dumps(responses))
                gh = root / "gh"
                gh.write_text(
                    '#!/usr/bin/env node\nconst fs=require("node:fs");const key=process.argv[3].replace("repos/example/plugin/", "");console.log(JSON.stringify(JSON.parse(fs.readFileSync(process.env.JPT_RESPONSES))[key]));\n'
                )
                gh.chmod(0o755)
                output = root / "outputs"
                result = subprocess.run(
                    ["node", "--input-type=module"],
                    input=program,
                    text=True,
                    check=False,
                    capture_output=True,
                    env={
                        **os.environ,
                        "PATH": str(root) + os.pathsep + os.environ["PATH"],
                        "REPOSITORY": "example/plugin",
                        "RUN_ID": "1",
                        "PR_NUMBER": "",
                        "BOT_LOGIN": "renovate[bot]",
                        "DEFAULT_BRANCH": "main",
                        "GITHUB_OUTPUT": str(output),
                        "JPT_RESPONSES": str(root / "responses.json"),
                    },
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn(
                    "ready="
                    + str(scenario in {"toolkit", "failed-validation"}).lower(),
                    output.read_text(),
                )
