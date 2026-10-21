"""Consumer isolation, input boundaries and evidence trust regressions."""

import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from consumer_fixture import ConsumerTestCase, create_consumer

from jellyfin_plugin_toolkit import tasks
from jellyfin_plugin_toolkit.checks.architecture import toolkit_graph
from jellyfin_plugin_toolkit.ci import evidence, planning
from jellyfin_plugin_toolkit.ci.policy import ValidationPolicy
from jellyfin_plugin_toolkit.common.artifacts import web_inputs
from jellyfin_plugin_toolkit.common.source_inventory import source_files
from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.scaffold import toml


class SweepTests(ConsumerTestCase):
    def write_config(self, selected):
        (self.root / "plugin.toml").write_text(toml(selected))

    def test_operations_and_policies_stay_with_the_supplied_consumer(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.ctx.config.data()
            config["validation"] = {"groups": ["backend", "artifacts"], "rules": []}
            other = create_consumer(Path(directory), config)
            for ctx in (self.ctx, other, self.ctx):
                ctx.config.operations = {
                    "test-custom": {"commands": [["fixture", "{root}"]]}
                }
                with patch.object(tasks, "run") as run:
                    tasks.operation(ctx, "test-custom")
                self.assertEqual(ctx.root, run.call_args.kwargs["cwd"])
                self.assertEqual(str(ctx.root), run.call_args.args[1])
                self.assertEqual(ctx.groups, ValidationPolicy.from_context(ctx).groups)
            with self.assertRaises(TypeError):
                ValidationPolicy.from_context(other).job_names["backend"] = frozenset()

    def test_imports_and_help_need_no_consumer_and_preserve_argv(self):
        code = """
import importlib, pkgutil, jellyfin_plugin_toolkit, sys
for info in pkgutil.walk_packages(jellyfin_plugin_toolkit.__path__, jellyfin_plugin_toolkit.__name__ + '.'):
    if not info.name.endswith(".__main__"):
        importlib.import_module(info.name)
from jellyfin_plugin_toolkit.cli import main
before = list(sys.argv)
for args in (['--help'], ['ci', '--help'], ['release', '--help']):
    try: main(args)
    except SystemExit as error: assert error.code == 0
    assert sys.argv == before
"""
        env = dict(os.environ)
        env.pop("JELLYFIN_PLUGIN_ROOT", None)
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [sys.executable, "-c", code],
                cwd=directory,
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def test_dependencies_are_acyclic_including_local_imports(self):
        graph = toolkit_graph(Path(__file__).resolve().parents[1] / "src")
        self.assertIn(
            "jellyfin_plugin_toolkit.checks.formatting",
            graph["jellyfin_plugin_toolkit.tasks"],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "jellyfin_plugin_toolkit"
            package.mkdir()
            (package / "a.py").write_text(
                "def f():\n    import jellyfin_plugin_toolkit.b\n"
            )
            (package / "b.py").write_text("import jellyfin_plugin_toolkit.a\n")
            with self.assertRaisesRegex(ValueError, "cycle"):
                toolkit_graph(root)

    def test_configuration_rejects_escaping_inputs_and_outputs(self):
        invalid = [
            ("frontend", "entries", {"../../outside.ts": "bundle.js"}),
            ("frontend", "entries", {"index.ts": "../bundle.js"}),
            ("frontend", "assets", "../outside"),
            (
                "frontend",
                "extra_assets",
                [{"source": "README.md", "name": "../NOTICE"}],
            ),
            ("browser", "groups", "../groups.json"),
            ("operations", "test-x", {"commands": [["ok"]], "cwd": "../outside"}),
            ("distribution", "notices", [{"source": "LICENSE", "name": "../LICENSE"}]),
            (
                "distribution",
                "payload_notices",
                [{"source": "../LICENSE", "name": "LICENSE"}],
            ),
        ]
        for section, key, value in invalid:
            with self.subTest(section=section, key=key):
                selected = self.ctx.config.data()
                selected.setdefault(section, {})[key] = value
                self.write_config(selected)
                with self.assertRaises(ValueError):
                    Context.load(self.root)

    def test_empty_commands_and_jobs_cannot_disable_validation(self):
        for key in ("job_names", "aggregate_jobs", "artifact_jobs", "commands"):
            with self.subTest(key=key):
                selected = self.ctx.config.data()
                if key == "commands":
                    selected["operations"] = {"test-x": {"commands": []}}
                else:
                    selected["validation"][key] = (
                        [] if key == "artifact_jobs" else {"backend": []}
                    )
                self.write_config(selected)
                with self.assertRaises(ValueError):
                    Context.load(self.root)
        selected = self.ctx.config.data()
        selected["validation"]["aggregate_jobs"] = {"backend": []}
        with self.assertRaises(ValueError):
            planning.finish(
                Context(self.root, selected),
                {"required": ["backend"], "reused": {}},
                {},
            )

    def test_explicit_sources_must_exist_and_cannot_be_symlinks(self):
        self.ctx.config.source = {"roots": ["optional"], "files": ["missing.txt"]}
        self.write_config(self.ctx.config)
        with self.assertRaisesRegex(ValueError, "Missing maintained"):
            source_files(self.root)
        (self.root / "real.txt").write_text("source")
        (self.root / "missing.txt").symlink_to(self.root / "real.txt")
        with self.assertRaisesRegex(ValueError, "symlink"):
            source_files(self.root)
        (self.root / "missing.txt").unlink()
        (self.root / "missing.txt").write_text("source")
        self.assertIn(Path("missing.txt"), source_files(self.root))

    def test_source_roots_and_nested_directories_cannot_be_links(self):
        for root_link in (False, True):
            with (
                self.subTest(root_link=root_link),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                ctx = create_consumer(root)
                (root / "actual").mkdir()
                if root_link:
                    (root / "inputs").symlink_to(
                        root / "actual", target_is_directory=True
                    )
                else:
                    (root / "inputs").mkdir()
                    (root / "inputs/link").symlink_to(
                        root / "actual", target_is_directory=True
                    )
                ctx.config.source = {"roots": ["inputs"]}
                (root / "plugin.toml").write_text(toml(ctx.config))
                with self.assertRaisesRegex(ValueError, "symlink"):
                    source_files(root)

    def test_external_assets_are_fingerprinted_and_must_be_maintained(self):
        self.ctx.config.frontend.extra_assets = [
            {"source": "legal/NOTICE", "name": "NOTICE"}
        ]
        self.ctx.config.source = {"roots": ["frontend", "legal"]}
        (self.root / "legal").mkdir()
        asset = self.root / "legal/NOTICE"
        asset.write_text("first")
        self.write_config(self.ctx.config)
        first = web_inputs(self.root)
        asset.write_text("second")
        self.assertNotEqual(first, web_inputs(self.root))
        self.ctx.config.source.roots.remove("legal")
        self.write_config(self.ctx.config)
        with self.assertRaisesRegex(ValueError, "outside maintained"):
            web_inputs(self.root)

    def test_malformed_receipts_cannot_supply_reuse_evidence(self):
        run = {
            "id": 100,
            "repository": {"full_name": "owner/repo"},
            "head_repository": {"full_name": "owner/repo"},
            "event": "push",
            "head_branch": "main",
            "head_sha": "source",
        }
        valid = {
            "schema": 4,
            "run_id": "100",
            "repository": "owner/repo",
            "run_head_sha": "source",
            "inputs": {},
            "groups": {},
            "required": [],
            "policy": "policy",
        }
        malformed = [
            [],
            dict(valid, reused={"backend": []}),
            dict(valid, full_baseline=[]),
            dict(valid, required=[{}]),
            dict(valid, inputs=[]),
            dict(valid, run_attempt=False),
            dict(valid, full="false"),
            dict(valid, release=[]),
        ]
        for receipt in malformed:
            with self.subTest(receipt=receipt):
                payload = io.BytesIO()
                with zipfile.ZipFile(payload, "w") as archive:
                    archive.writestr("validation.json", json.dumps(receipt))
                with patch.object(
                    evidence,
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
                        payload.getvalue(),
                    ],
                ):
                    self.assertIsNone(evidence.read_receipt("owner/repo", run))

    def test_release_receipt_and_job_must_match_latest_attempt(self):
        run = {
            "id": 100,
            "repository": {"full_name": "owner/repo"},
            "head_repository": {"full_name": "owner/repo"},
            "event": "push",
            "head_branch": "main",
            "path": ".github/workflows/validate.yml",
            "head_sha": "source",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 2,
        }
        receipt = {
            "run_head_sha": "source",
            "run_attempt": 1,
            "validated_at": datetime.now(UTC).isoformat(),
        }
        for receipt_attempt, job_attempt in ((1, 2), (2, 1), (2, 2)):
            receipt["run_attempt"] = receipt_attempt
            job = {
                "name": "validate",
                "status": "completed",
                "conclusion": "success",
                "run_attempt": job_attempt,
            }
            with (
                self.subTest(receipt=receipt_attempt, job=job_attempt),
                patch.object(evidence, "api", side_effect=[run, {"jobs": [job]}]),
            ):
                if receipt_attempt == job_attempt == 2:
                    evidence.verify_run("owner/repo", "100", receipt)
                else:
                    with self.assertRaises(ValueError):
                        evidence.verify_run("owner/repo", "100", receipt)
