import tempfile
import unittest
from pathlib import Path

from jellyfin_plugin_toolkit.archive import pack


class ArchiveTests(unittest.TestCase):
    def test_repeated_pack_is_deterministic_and_ignores_build_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "version.txt").write_text("1.0.0\n")
            (root / "module.py").write_text("x = 1\n")
            first, digest = pack(root, root / "dist")
            expected = first.read_bytes()
            (root / ".build").mkdir()
            (root / ".build/ignored").write_text("temporary")
            second, new_digest = pack(root, root / "dist")
            self.assertEqual(digest, new_digest)
            self.assertEqual(expected, second.read_bytes())

    def test_release_rejects_uncommitted_sources(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "version.txt").write_text("1.0.0\n")
            (root / ".git/info/exclude").write_text("dist/\n")
            subprocess.run(["git", "-C", str(root), "add", "version.txt"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-qm",
                    "baseline",
                ],
                check=True,
            )
            pack(root, root / "dist", release=True)
            (root / "unexpected.py").write_text("unexpected = True\n")
            with self.assertRaisesRegex(ValueError, "clean committed"):
                pack(root, root / "dist", release=True)
