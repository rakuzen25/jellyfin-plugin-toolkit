"""The verified toolkit cache survives imports and concurrent CLI readers."""

import hashlib
import io
import json
import os
import selectors
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ToolkitBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copyfile(ROOT / "bootstrap/plugin", self.root / "plugin")
        self.files = {
            "toolkit-release.json": json.dumps({"schema": 1, "version": "1.0.0"}),
            "jellyfin_plugin_toolkit/__init__.py": "",
            "jellyfin_plugin_toolkit/context.py": "VALUE = 'verified'\n",
            "jellyfin_plugin_toolkit/late.py": "VALUE = 'late import'\n",
            "jellyfin_plugin_toolkit/cli.py": """
import json
import os
import subprocess
import sys
from . import context

def main(argv):
    if argv == ['hold']:
        print('ready', flush=True)
        sys.stdin.readline()
        from . import late
        print(late.VALUE, flush=True)
    else:
        subprocess.run([sys.executable, '-c', '''
import json, os, sys
from jellyfin_plugin_toolkit import late
print(json.dumps({'value': late.VALUE, 'disabled': sys.dont_write_bytecode,
                  'policy': os.environ.get('PYTHONDONTWRITEBYTECODE')}))
'''], check=True, env={**os.environ, 'PYTHONPATH': os.environ['JELLYFIN_PLUGIN_TOOLKIT_ROOT']})
""",
            "node/build.mjs": "",
            "msbuild/Plugin.props": "<Project />",
            "ui/lifecycle.ts": "",
            "LICENSE": "fixture license",
            "NOTICE": "fixture notice",
        }
        self.files.update(
            {
                "bootstrap/plugin": (ROOT / "bootstrap/plugin").read_text(),
                "jellyfin_plugin_toolkit/bootstrap.py": (
                    ROOT / "jellyfin_plugin_toolkit/bootstrap.py"
                ).read_text(),
                "toolkit-bindings.json": (ROOT / "toolkit-bindings.json").read_text(),
                **{
                    "configs/" + name: "fixture"
                    for name in (
                        "eslint.mjs",
                        "ruff.toml",
                        "prettier.mjs",
                        "tsconfig.json",
                    )
                },
            }
        )
        self.files["toolkit-files.json"] = json.dumps(
            {
                "schema": 1,
                "files": {
                    name: hashlib.sha256(content.encode()).hexdigest()
                    for name, content in self.files.items()
                },
            }
        )
        self.archive = self.root / "build-support/toolkit.tar.gz"
        self.archive.parent.mkdir()
        with tarfile.open(self.archive, "w:gz") as package:
            for name, content in self.files.items():
                data = content.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                package.addfile(member, io.BytesIO(data))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.root / "toolkit.lock.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "repository": "fixture/toolkit",
                    "version": "v1.0.0",
                    "sha256": digest,
                }
            )
        )
        self.target = self.root / ".build/toolkit" / digest
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("JELLYFIN_PLUGIN_")
            and key
            not in {
                "PYTHONDONTWRITEBYTECODE",
                "PYTHONPYCACHEPREFIX",
                "PYTHONPATH",
                "GITHUB_ENV",
            }
        }

    def invoke(self, *args, **env):
        return subprocess.run(
            [sys.executable, str(self.root / "plugin"), *args],
            cwd=self.root,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def assert_verified_cache(self):
        self.assertEqual(
            set(self.files),
            {
                path.relative_to(self.target).as_posix()
                for path in self.target.rglob("*")
                if path.is_file()
            },
        )
        for name, content in self.files.items():
            self.assertEqual(content, (self.target / name).read_text(), name)

    def test_repeated_cli_imports_preserve_the_verified_installation(self):
        first = self.invoke()
        self.assertEqual(0, first.returncode, first.stderr)
        self.assert_verified_cache()
        source = self.target / "jellyfin_plugin_toolkit/context.py"
        before = source.stat()
        second = self.invoke()
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(before.st_ino, source.stat().st_ino)
        self.assertEqual(before.st_mtime_ns, source.stat().st_mtime_ns)
        self.assert_verified_cache()

    def test_child_python_process_inherits_bytecode_suppression(self):
        result = self.invoke(PYTHONDONTWRITEBYTECODE="0")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {"value": "late import", "disabled": True, "policy": "1"},
            json.loads(result.stdout),
        )
        self.assert_verified_cache()

    def test_another_invocation_does_not_replace_an_active_readers_files(self):
        with subprocess.Popen(
            [sys.executable, str(self.root / "plugin"), "hold"],
            cwd=self.root,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as reader:
            try:
                assert reader.stdout is not None
                with selectors.DefaultSelector() as selector:
                    selector.register(reader.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=10), "reader did not start")
                self.assertEqual("ready\n", reader.stdout.readline())
                with (
                    self.target / "jellyfin_plugin_toolkit/context.py"
                ).open() as held:
                    result = self.invoke()
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(
                        os.fstat(held.fileno()).st_ino,
                        (self.target / "jellyfin_plugin_toolkit/context.py")
                        .stat()
                        .st_ino,
                    )
                output, error = reader.communicate(input="\n", timeout=10)
                self.assertEqual(0, reader.returncode, error)
                self.assertEqual("late import\n", output)
                self.assert_verified_cache()
            finally:
                if reader.poll() is None:
                    reader.kill()
                    reader.communicate(timeout=10)

    def test_install_exports_bytecode_policy_for_subsequent_ci_steps(self):
        environment = self.root / "github-env"
        result = self.invoke("_install", GITHUB_ENV=str(environment))
        self.assertEqual(0, result.returncode, result.stderr)
        exported = dict(
            line.split("=", 1) for line in environment.read_text().splitlines()
        )
        self.assertEqual("1", exported["PYTHONDONTWRITEBYTECODE"])
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "from jellyfin_plugin_toolkit import context; print(context.VALUE)",
            ],
            env={**self.env, **exported},
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, child.returncode, child.stderr)
        self.assertEqual("verified\n", child.stdout)
        self.assert_verified_cache()

    def test_altered_archive_is_rejected_without_replacing_the_working_cache(self):
        self.assertEqual(0, self.invoke().returncode)
        self.archive.write_bytes(self.archive.read_bytes() + b"altered")
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assert_verified_cache()

    def test_modified_or_extra_installed_files_are_still_repaired(self):
        self.assertEqual(0, self.invoke().returncode)
        (self.target / "jellyfin_plugin_toolkit/context.py").write_text("altered")
        (self.target / "unexpected.py").write_text("unexpected")
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_verified_cache()

    def test_cached_bootstrap_is_never_executed_before_verification(self):
        self.assertEqual(0, self.invoke().returncode)
        (self.target / "jellyfin_plugin_toolkit/bootstrap.py").write_text(
            "raise RuntimeError('UNVERIFIED CACHE EXECUTED')"
        )
        (self.target / "jellyfin_plugin_toolkit/cli.py").write_text(
            "raise RuntimeError('UNVERIFIED CLI EXECUTED')"
        )
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_verified_cache()

    def test_missing_or_unknown_protocol_is_rejected_without_activation(self):
        for code in [
            None,
            "BOOTSTRAP_PROTOCOL = 2\ndef main(*args): raise RuntimeError('DISPATCHED')\n",
        ]:
            with self.subTest(code=code):
                files = dict(self.files)
                files.pop("toolkit-files.json")
                if code is None:
                    files.pop("jellyfin_plugin_toolkit/bootstrap.py")
                else:
                    files["jellyfin_plugin_toolkit/bootstrap.py"] = code
                files["toolkit-files.json"] = json.dumps(
                    {
                        "schema": 1,
                        "files": {
                            name: hashlib.sha256(data.encode()).hexdigest()
                            for name, data in files.items()
                        },
                    }
                )
                with tarfile.open(self.archive, "w:gz") as package:
                    for name, text in files.items():
                        member = tarfile.TarInfo(name)
                        data = text.encode()
                        member.size = len(data)
                        package.addfile(member, io.BytesIO(data))
                lock = json.loads((self.root / "toolkit.lock.json").read_text())
                lock["sha256"] = hashlib.sha256(self.archive.read_bytes()).hexdigest()
                (self.root / "toolkit.lock.json").write_text(json.dumps(lock))
                result = self.invoke()
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Unsupported toolkit bootstrap protocol", result.stderr)
                self.assertFalse((self.root / ".build/toolkit/current").exists())


if __name__ == "__main__":
    unittest.main()
