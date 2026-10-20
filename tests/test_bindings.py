"""Upgrade behavior is exercised with independent release archives and consumers."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jellyfin_plugin_toolkit import bindings
from jellyfin_plugin_toolkit.maintenance import editor
from jellyfin_plugin_toolkit.workflows import render

ROOT = Path(__file__).resolve().parents[1]


def release(root, version, revision, launcher=None, contract=None, cli=b"fixture"):
    files = {
        "toolkit-release.json": json.dumps(
            {"schema": 1, "version": version, "revision": revision}
        ).encode(),
        "bootstrap/plugin": launcher or (ROOT / "bootstrap/plugin").read_bytes(),
        "toolkit-bindings.json": json.dumps(contract or bindings.DESCRIPTOR).encode(),
        **{name: b"fixture" for name in bindings.INTEGRATIONS.values()},
        **{
            name: b"fixture"
            for name in [
                "jellyfin_plugin_toolkit/__init__.py",
                "jellyfin_plugin_toolkit/cli.py",
                "jellyfin_plugin_toolkit/context.py",
                "ui/lifecycle.ts",
                "LICENSE",
                "NOTICE",
            ]
        },
        **{
            ".github/workflows/" + name: text.encode()
            for name, text in render(
                "example/toolkit", revision, version="v" + version
            ).items()
        },
    }
    files["jellyfin_plugin_toolkit/__init__.py"] = b""
    files["jellyfin_plugin_toolkit/cli.py"] = cli
    files[".github/workflows/prepare-release.yml"] = b"fixture"
    files["toolkit-files.json"] = json.dumps(
        {
            "schema": 1,
            "files": {
                name: hashlib.sha256(data).hexdigest() for name, data in files.items()
            },
        }
    ).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    data = stream.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    cache = root / ".build/toolkit"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / (digest + ".tar.gz")).write_bytes(data)
    return {
        "schema": 1,
        "repository": "example/toolkit",
        "version": "v" + version,
        "sha256": digest,
    }


class BindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old = release(self.root, "1.0.0", "a" * 40)
        self.lock(self.old)
        (self.root / "plugin").write_bytes((ROOT / "bootstrap/plugin").read_bytes())
        (self.root / "plugin").chmod(0o755)
        self.workflows = self.root / ".github/workflows"
        self.workflows.mkdir(parents=True)
        for name, text in render("example/toolkit", "a" * 40, version="v1.0.0").items():
            (self.workflows / name).write_text(text)
        (self.root / "renovate.json").write_text(
            '{\n  "extends": ["github>example/toolkit#v1.0.0"],\n  "automerge": false\n}\n'
        )
        (self.root / ".gitignore").write_text(".build/\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.email=fixture@example.org",
            "commit",
            "-qm",
            "base",
        )
        self.base = self.git("rev-parse", "HEAD").decode().strip()

    def lock(self, value):
        (self.root / "toolkit.lock.json").write_text(json.dumps(value))

    def git(self, *args):
        return bindings.git(self.root, *args)

    def upgrade(self, version="3.0.0", contract=None):
        self.new = release(
            self.root,
            version,
            "b" * 40,
            (ROOT / "bootstrap/plugin").read_bytes() + b"\n# new release\n",
            contract,
        )
        self.lock(self.new)
        return bindings.plan(self.root, self.base)

    def test_preview_apply_and_idempotence_preserve_consumer_changes(self):
        workflow = self.workflows / "validate.yml"
        workflow.write_text(
            workflow.read_text().replace(
                "  pull_request:",
                "  pull_request:\n    branches: [main] # consumer policy",
            )
        )
        (self.root / "plugin.toml").write_text("# consumer-owned\n")
        changes = self.upgrade()
        self.assertEqual(
            "a" * 40,
            bindings.workflow_references(workflow.read_text(), "example/toolkit")[0][
                "revision"
            ],
        )
        bindings.apply(self.root, changes)
        self.assertIn("# consumer policy", workflow.read_text())
        self.assertIn("b" * 40, workflow.read_text())
        self.assertEqual("# consumer-owned\n", (self.root / "plugin.toml").read_text())
        self.assertIn('"automerge": false', (self.root / "renovate.json").read_text())
        self.assertEqual([], bindings.plan(self.root, self.base))
        self.assertEqual([], bindings.plan(self.root))

    def test_partially_updated_references_are_repaired(self):
        self.upgrade()
        workflow = self.workflows / "validate.yml"
        workflow.write_text(workflow.read_text().replace("a" * 40, "b" * 40))
        changes = bindings.plan(self.root, self.base)
        bindings.apply(self.root, changes)
        self.assertEqual([], bindings.plan(self.root))

    def test_local_launcher_edits_are_not_overwritten(self):
        original = (self.root / "plugin").read_bytes() + b"# local edit\n"
        (self.root / "plugin").write_bytes(original)
        with self.assertRaisesRegex(ValueError, "Local launcher edits"):
            self.upgrade()
        self.assertEqual(original, (self.root / "plugin").read_bytes())

    def test_same_release_launcher_repair_requires_explicit_flag(self):
        (self.root / "plugin").write_text("# edited\n")
        with self.assertRaises(SystemExit):
            bindings.main(["--root", str(self.root), "--apply"])
        self.assertEqual("# edited\n", (self.root / "plugin").read_text())
        self.assertEqual(
            0, bindings.main(["--root", str(self.root), "--apply", "--repair"])
        )
        self.assertEqual([], bindings.plan(self.root))

    def test_check_exit_status_and_permission_repair(self):
        self.assertEqual(0, bindings.main(["--root", str(self.root), "--check"]))
        (self.root / "plugin").chmod(0o644)
        self.assertEqual(1, bindings.main(["--root", str(self.root), "--check"]))
        bindings.apply(self.root, bindings.plan(self.root))
        self.assertEqual(0o755, (self.root / "plugin").stat().st_mode & 0o777)

    def test_group_writable_executable_checkout_does_not_have_drift(self):
        (self.root / "plugin").chmod(0o775)
        self.assertEqual([], bindings.plan(self.root))

    def test_invalid_contract_and_archive_never_change_source(self):
        original = (self.root / "plugin").read_bytes()
        with self.assertRaisesRegex(ValueError, "Unsupported binding"):
            self.upgrade(contract={"schema": 2, "script": "execute-me.py"})
        self.assertEqual(original, (self.root / "plugin").read_bytes())
        archive = self.root / ".build/toolkit" / (self.new["sha256"] + ".tar.gz")
        archive.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            bindings.plan(self.root, self.base)

    def test_existing_drift_in_base_is_rejected(self):
        path = self.workflows / "validate.yml"
        path.write_text(path.read_text().replace("a" * 40, "c" * 40))
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.email=fixture@example.org",
            "commit",
            "-qm",
            "drift",
        )
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(ValueError, "drift"):
            self.upgrade()

    def test_ambiguous_reference_is_rejected(self):
        path = self.workflows / "validate.yml"
        path.write_text(
            "jobs: {validate: {uses: example/toolkit/.github/workflows/validate.yml@"
            + "a" * 40
            + "}}\n"
        )
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            self.upgrade()

    def test_symlink_is_rejected_without_modifying_its_target(self):
        outside = self.root / "outside"
        outside.write_text("preserve")
        (self.root / "plugin").unlink()
        (self.root / "plugin").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            bindings.plan(self.root)
        self.assertEqual("preserve", outside.read_text())

    def test_replacement_failure_restores_all_originals(self):
        changes = self.upgrade()
        replace = os.replace
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            replace(source, destination)

        with patch.object(bindings.os, "replace", side_effect=fail_second):
            with self.assertRaises(KeyboardInterrupt):
                bindings.apply(self.root, changes)
        for item in changes:
            self.assertEqual(item.before, (self.root / item.name).read_bytes())
            self.assertEqual(
                item.before_mode, (self.root / item.name).stat().st_mode & 0o777
            )

    def test_concurrent_edit_is_rejected_before_any_replacement(self):
        changes = self.upgrade()
        (self.root / changes[-1].name).write_text("# concurrent edit\n")
        with self.assertRaisesRegex(ValueError, "changed during"):
            bindings.apply(self.root, changes)
        self.assertEqual(changes[0].before, (self.root / changes[0].name).read_bytes())

    def test_editor_is_opt_in_and_preserves_unrelated_settings(self):
        folder = self.root / ".vscode"
        folder.mkdir()
        settings = folder / "settings.json"
        settings.write_text('{"editor.fontSize": 17}')
        editor(self.root)
        self.assertEqual('{"editor.fontSize": 17}', settings.read_text())
        editor(self.root, True)
        self.assertEqual(17, json.loads(settings.read_text())["editor.fontSize"])

    def test_process_exit_retains_recoverable_transaction(self):
        changes = self.upgrade()
        script = """
import os, sys
from pathlib import Path
from jellyfin_plugin_toolkit import bindings
root = Path(sys.argv[1])
changes = bindings.plan(root, sys.argv[2])
replace = os.replace
count = 0
def interrupt(source, destination):
    global count
    count += 1
    if count == 2:
        os._exit(73)
    replace(source, destination)
bindings.os.replace = interrupt
bindings.apply(root, changes)
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.root), self.base],
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.assertEqual(73, result.returncode)
        with self.assertRaisesRegex(ValueError, "Interrupted"):
            bindings.plan(self.root, self.base)
        self.assertEqual(0, bindings.main(["--root", str(self.root), "--recover"]))
        for item in changes:
            self.assertEqual(item.before, (self.root / item.name).read_bytes())
        self.assertFalse(bindings.transaction_path(self.root).exists())

    def test_ruff_native_entrypoint_inherits_defaults_and_overrides(self):
        folder = self.root / ".build/toolkit/current/configs"
        folder.mkdir(parents=True)
        (folder / "ruff.toml").write_bytes((ROOT / "configs/ruff.toml").read_bytes())
        (self.root / "pyproject.toml").write_text(
            '[tool.ruff]\nextend = ".build/toolkit/current/configs/ruff.toml"\n[tool.ruff.lint]\nignore = ["F841"]\n'
        )
        (self.root / "example.py").write_text(
            "def example():\n    unused = 1\n    return missing\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--no-cache",
                "--output-format",
                "json",
                "example.py",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(["F821"], [item["code"] for item in json.loads(result.stdout)])

    def test_crlf_quoted_reference_preserves_surrounding_formatting(self):
        path = self.workflows / "validate.yml"
        text = path.read_text().replace(
            "uses: example/toolkit/.github/workflows/validate.yml@" + "a" * 40,
            'uses: "example/toolkit/.github/workflows/validate.yml@' + "a" * 40 + '"',
        )
        path.write_bytes(text.replace("\n", "\r\n").encode())
        changes = self.upgrade()
        bindings.apply(self.root, changes)
        actual = path.read_bytes()
        self.assertIn(('" # v3.0.0\r\n').encode(), actual)
        self.assertNotIn(b"\n", actual.replace(b"\r\n", b""))

    def test_presets_only_rewrite_top_level_extends(self):
        original = '{"extends":["github>example/toolkit#v1.0.0"],"description":"github>example/toolkit#v1.0.0","packageRules":[{"extends":["consumer-policy"]}]}'
        new = bindings.rewrite_preset(
            original, self.old, {**self.old, "version": "v2.0.0"}
        )
        self.assertIn('"description":"github>example/toolkit#v1.0.0"', new)
        self.assertEqual(["github>example/toolkit#v2.0.0"], json.loads(new)["extends"])


class LauncherTrustTests(unittest.TestCase):
    setUp = BindingTests.setUp
    lock = BindingTests.lock
    git = BindingTests.git

    # Exercise the actual process boundary, including argparse's equals spelling.
    def test_base_release_is_selected_before_importing_the_proposed_release(self):
        old = release(
            self.root,
            "1.0.0",
            "a" * 40,
            cli=b'def main():\n    print("TRUSTED BASE")\n',
        )
        self.lock(old)
        self.git("add", "toolkit.lock.json")
        self.git(
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.org",
            "commit",
            "-qm",
            "trusted base",
        )
        base = self.git("rev-parse", "HEAD").decode().strip()
        new = release(
            self.root,
            "2.0.0",
            "b" * 40,
            cli=b'raise RuntimeError("TARGET CODE EXECUTED")\n',
        )
        self.lock(new)
        result = subprocess.run(
            [
                sys.executable,
                str(self.root / "plugin"),
                "toolkit",
                "sync",
                "--base-ref=" + base,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("TRUSTED BASE\n", result.stdout)
        installed = self.root / ".build/toolkit" / old["sha256"]
        self.assertFalse(any(installed.rglob("*.pyc")))

    def test_active_release_is_implicit_base_after_lock_changes(self):
        old = release(
            self.root,
            "1.0.0",
            "a" * 40,
            cli=b'def main():\n    print("TRUSTED ACTIVE BASE")\n',
        )
        self.lock(old)
        bindings.bootstrap_api()["install"](self.root)
        new = release(
            self.root,
            "2.0.0",
            "b" * 40,
            cli=b'raise RuntimeError("TARGET CODE EXECUTED")\n',
        )
        self.lock(new)
        result = subprocess.run(
            [sys.executable, str(self.root / "plugin"), "toolkit", "sync"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("TRUSTED ACTIVE BASE\n", result.stdout)
