"""Toolkit releases bind immutable source, archive inventory and retry bytes."""

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jellyfin_plugin_toolkit import toolkit_release as release
from jellyfin_plugin_toolkit.archive import pack


class ToolkitReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "version.txt").write_text("0.1.0\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".git/info/exclude").write_text("dist/\n")
        subprocess.run(["git", "-C", str(self.root), "add", "version.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.sha = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()
        self.directory = self.root / "dist"
        self.archive, _ = pack(self.root, self.directory, release=True)
        self.draft = {"id": 123, "draft": True, "tag_name": "v0.1.0", "assets": []}

    def resolve(self, **kwargs):
        with (
            patch.object(release, "release_for_tag", return_value=self.draft),
            patch.object(release, "tag_commit", return_value=self.sha),
        ):
            return release.resolve("example/toolkit", "v0.1.0", **kwargs)

    def test_draft_resolves_only_matching_immutable_source(self):
        self.assertEqual((self.draft, self.sha), self.resolve(sha=self.sha))
        with self.assertRaisesRegex(ValueError, "moved"):
            self.resolve(sha="f" * 40)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.resolve(sha="main")
        for draft in [None, {"draft": False}]:
            with (
                patch.object(release, "release_for_tag", return_value=draft),
                self.assertRaisesRegex(ValueError, "unpublished"),
            ):
                release.resolve("example/toolkit", "v0.1.0")

    def test_archive_must_match_version_revision_and_checksum(self):
        hashes = release.artifact_hashes(self.directory, "v0.1.0", self.sha)
        self.assertEqual({self.archive.name, "SHA256SUMS"}, set(hashes))
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            release.artifact_hashes(self.directory, "v0.1.0", "f" * 40)
        with self.assertRaises(FileNotFoundError):
            release.artifact_hashes(self.directory, "v0.2.0", self.sha)
        self.archive.write_bytes(self.archive.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "checksum"):
            release.artifact_hashes(self.directory, "v0.1.0", self.sha)

    def test_inventory_tampering_is_rejected_even_with_updated_outer_checksum(self):
        import io
        import tarfile

        with tarfile.open(self.archive, "r:gz") as archive:
            files = {
                m.name: archive.extractfile(m).read() for m in archive.getmembers()
            }
        files["version.txt"] += b"\n"
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, contents in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(contents)
                archive.addfile(entry, io.BytesIO(contents))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.directory / "SHA256SUMS").write_text(f"{digest}  {self.archive.name}\n")
        with self.assertRaisesRegex(ValueError, "inventory checksum mismatch"):
            release.artifact_hashes(self.directory, "v0.1.0", self.sha)

    def test_upload_retry_preserves_identical_assets_and_uploads_only_missing_bytes(
        self,
    ):
        self.draft["assets"] = [{"name": self.archive.name}]

        def download(command, **kwargs):
            if command[2] == "download":
                directory = Path(command[command.index("--dir") + 1])
                (directory / self.archive.name).write_bytes(self.archive.read_bytes())

        with (
            patch.object(release, "resolve", return_value=(self.draft, self.sha)),
            patch.object(release.subprocess, "run", side_effect=download) as run,
        ):
            release.upload(self.directory, "example/toolkit", "v0.1.0", self.sha)
        upload = run.call_args_list[-1].args[0]
        self.assertEqual(["gh", "release", "upload", "v0.1.0"], upload[:4])
        self.assertIn(str(self.directory / "SHA256SUMS"), upload)
        self.assertNotIn(str(self.archive), upload)
        self.assertNotIn("--clobber", upload)

    def test_complete_retry_does_not_upload_and_conflicts_never_overwrite(self):
        self.draft["assets"] = [
            {"name": name} for name in [self.archive.name, "SHA256SUMS"]
        ]
        for corrupt in [False, True]:
            with self.subTest(corrupt=corrupt):

                def download(command, **kwargs):
                    name = command[command.index("--pattern") + 1]
                    target = Path(command[command.index("--dir") + 1]) / name
                    target.write_bytes(
                        b"conflict" if corrupt else (self.directory / name).read_bytes()
                    )

                with (
                    patch.object(
                        release, "resolve", return_value=(self.draft, self.sha)
                    ),
                    patch.object(
                        release.subprocess, "run", side_effect=download
                    ) as run,
                ):
                    if corrupt:
                        with self.assertRaisesRegex(ValueError, "Conflicting"):
                            release.upload(
                                self.directory, "example/toolkit", "v0.1.0", self.sha
                            )
                    else:
                        release.upload(
                            self.directory, "example/toolkit", "v0.1.0", self.sha
                        )
                    self.assertTrue(
                        all(c.args[0][2] == "download" for c in run.call_args_list)
                    )

    def test_publication_during_verification_stops_upload(self):
        with (
            patch.object(
                release,
                "resolve",
                side_effect=[(self.draft, self.sha), ValueError("published")],
            ),
            patch.object(release.subprocess, "run") as run,
            self.assertRaisesRegex(ValueError, "published"),
        ):
            release.upload(self.directory, "example/toolkit", "v0.1.0", self.sha)
        run.assert_not_called()
