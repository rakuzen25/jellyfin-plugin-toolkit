import hashlib
import io
import json
import os
import runpy
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
install = runpy.run_path(str(ROOT / "bootstrap/plugin"))["install"]


class BootstrapTests(unittest.TestCase):
    def archive(self, root, members, *, exclude=()):
        archive = root / "candidate.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            required = [
                "jellyfin_plugin_toolkit/__init__.py",
                "jellyfin_plugin_toolkit/cli.py",
                "jellyfin_plugin_toolkit/context.py",
                "node/build.mjs",
                "msbuild/Plugin.props",
                "bootstrap/plugin",
                "toolkit-bindings.json",
                "configs/eslint.mjs",
                "configs/ruff.toml",
                "configs/prettier.mjs",
                "configs/tsconfig.json",
                "ui/lifecycle.ts",
                "LICENSE",
                "NOTICE",
            ]
            files = {name: b"fixture source" for name in required}
            files.update(
                {
                    "toolkit-release.json": json.dumps(
                        {"schema": 1, "version": "1.0.0", "revision": "a" * 40}
                    ).encode(),
                    **members,
                }
            )
            for name in exclude:
                del files[name]
            files["toolkit-files.json"] = json.dumps(
                {
                    "schema": 1,
                    "files": {
                        name: hashlib.sha256(data).hexdigest()
                        for name, data in files.items()
                    },
                }
            ).encode()
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                package.addfile(entry, io.BytesIO(data))
        lock = {
            "schema": 1,
            "repository": "example/toolkit",
            "version": "v1.0.0",
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        (root / "toolkit.lock.json").write_text(json.dumps(lock))
        return archive, lock

    def test_archives_must_include_the_binding_contract_and_presets(self):
        for name in [
            "toolkit-bindings.json",
            "bootstrap/plugin",
            "configs/eslint.mjs",
            "configs/ruff.toml",
            "configs/prettier.mjs",
            "configs/tsconfig.json",
        ]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, _ = self.archive(root, {}, exclude=[name])
                with patch.dict(
                    os.environ, {"JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(archive)}
                ):
                    with self.assertRaisesRegex(ValueError, "incomplete"):
                        install(root)
                self.assertFalse((root / ".build/toolkit/current").exists())

    def test_rejects_corruption_and_preserves_current_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, lock = self.archive(root, {"package.py": b"expected"})
            with patch.dict(
                os.environ, {"JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(archive)}
            ):
                target = install(root)
                archive.write_bytes(b"corrupt")
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    install(root)
                self.assertEqual(b"expected", (target / "package.py").read_bytes())
                self.assertEqual(target, (root / ".build/toolkit/current").resolve())

    def test_incomplete_archive_does_not_replace_current_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, lock = self.archive(root, {"package.py": b"working"})
            with patch.dict(
                os.environ, {"JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(archive)}
            ):
                working = install(root)
                with tarfile.open(archive, "w:gz") as package:
                    data = b'{"schema":1,"version":"1.0.0"}'
                    member = tarfile.TarInfo("toolkit-release.json")
                    member.size = len(data)
                    package.addfile(member, io.BytesIO(data))
                lock["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
                (root / "toolkit.lock.json").write_text(json.dumps(lock))
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    install(root)
                self.assertEqual(working, (root / ".build/toolkit/current").resolve())
                self.assertEqual(b"working", (working / "package.py").read_bytes())

    def test_corrupt_embedded_archive_is_not_hidden_by_a_valid_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _ = self.archive(root, {"package.py": b"working"})
            with patch.dict(
                os.environ, {"JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(archive)}
            ):
                target = install(root)
            (root / "build-support").mkdir()
            (root / "build-support/toolkit.tar.gz").write_bytes(
                archive.read_bytes() + b"altered"
            )
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                install(root)
            self.assertEqual(b"working", (target / "package.py").read_bytes())

    def test_tampered_cache_is_repaired_from_verified_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _ = self.archive(root, {"package.py": b"expected"})
            with patch.dict(
                os.environ, {"JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(archive)}
            ):
                target = install(root)
                (target / "package.py").write_bytes(b"modified")
                install(root)
                self.assertEqual(b"expected", (target / "package.py").read_bytes())

    def test_unexpected_cached_python_module_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _ = self.archive(root, {"package.py": b"expected"})
            with patch.dict(
                os.environ, {"JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(archive)}
            ):
                target = install(root)
                (target / "injected.py").write_text("raise RuntimeError()")
                install(root)
                self.assertFalse((target / "injected.py").exists())

    def test_rejects_traversal_without_writing_outside_cache(self):
        for name in [
            "../escape",
            "/absolute",
            "folder/../../escape",
            "folder\\escape",
            "folder/./escape",
            "./escape",
        ]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, _ = self.archive(root, {name: b"bad"})
                with (
                    patch.dict(
                        os.environ, {"JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(archive)}
                    ),
                    self.assertRaisesRegex(ValueError, "Unsafe"),
                ):
                    install(root)
                self.assertFalse((root / "escape").exists())

    def test_rejects_link_and_duplicate_members(self):
        for kind in ["link", "duplicate"]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, lock = self.archive(root, {"package.py": b"good"})
                with tarfile.open(root / "bad.tar", "w:gz") as package:
                    member = tarfile.TarInfo("package.py")
                    if kind == "link":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/tmp/outside"
                        package.addfile(member)
                    else:
                        package.addfile(member)
                        package.addfile(member)
                bad = root / "bad.tar"
                lock["sha256"] = hashlib.sha256(bad.read_bytes()).hexdigest()
                (root / "toolkit.lock.json").write_text(json.dumps(lock))
                with (
                    patch.dict(
                        os.environ, {"JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(bad)}
                    ),
                    self.assertRaises(ValueError),
                ):
                    install(root)


if __name__ == "__main__":
    unittest.main()
