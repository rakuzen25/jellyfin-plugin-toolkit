"""Toolkit releases bind immutable source, archive inventory and retry bytes."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jellyfin_plugin_toolkit import toolkit_release as release


class ToolkitReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        import io
        import tarfile
        import zipfile

        self.sha = "a" * 40
        self.directory = self.root / "dist"
        self.directory.mkdir()
        self.archive = self.directory / "jellyfin_plugin_toolkit-0.1.0-py3-none-any.whl"
        metadata = (
            b"Metadata-Version: 2.3\nName: jellyfin-plugin-toolkit\nVersion: 0.1.0\n"
        )
        with zipfile.ZipFile(self.archive, "w") as wheel:
            wheel.writestr("jellyfin_plugin_toolkit-0.1.0.dist-info/METADATA", metadata)
        with tarfile.open(
            self.directory / "jellyfin_plugin_toolkit-0.1.0.tar.gz", "w:gz"
        ) as source:
            item = tarfile.TarInfo("jellyfin_plugin_toolkit-0.1.0/PKG-INFO")
            item.size = len(metadata)
            source.addfile(item, io.BytesIO(metadata))
        with patch.object(release, "package_version", return_value="0.1.0"):
            release.manifest(self.directory, self.sha)
        self.draft: dict[str, Any] = {
            "id": 123,
            "draft": True,
            "tag_name": "v0.1.0",
            "assets": [],
        }

    def resolve(self, **kwargs: Any) -> tuple[dict[str, Any], str]:
        with (
            patch.object(release, "release_for_tag", return_value=self.draft),
            patch.object(release, "tag_commit", return_value=self.sha),
        ):
            return release.resolve("example/toolkit", "v0.1.0", **kwargs)

    def test_draft_resolves_only_matching_immutable_source(self) -> None:
        self.assertEqual((self.draft, self.sha), self.resolve(sha=self.sha))
        with self.assertRaisesRegex(ValueError, "moved"):
            self.resolve(sha="f" * 40)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.resolve(sha="main")
        with (
            patch.object(release, "release_for_tag", return_value=None),
            self.assertRaisesRegex(ValueError, "not visible.*App token"),
        ):
            release.resolve("example/toolkit", "v0.1.0")
        with (
            patch.object(release, "release_for_tag", return_value={"draft": False}),
            self.assertRaisesRegex(ValueError, "unpublished"),
        ):
            release.resolve("example/toolkit", "v0.1.0")

    def test_archive_must_match_version_revision_and_checksum(self) -> None:
        hashes = release.artifact_hashes(self.directory, "v0.1.0", self.sha)
        self.assertEqual(
            {
                self.archive.name,
                "jellyfin_plugin_toolkit-0.1.0.tar.gz",
                "SHA256SUMS",
                "build-provenance.json",
            },
            set(hashes),
        )
        with self.assertRaisesRegex(ValueError, "provenance"):
            release.artifact_hashes(self.directory, "v0.1.0", "f" * 40)
        with self.assertRaises(FileNotFoundError):
            release.artifact_hashes(self.directory, "v0.2.0", self.sha)
        self.archive.write_bytes(self.archive.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "checksums"):
            release.artifact_hashes(self.directory, "v0.1.0", self.sha)

    def test_source_provenance_tampering_is_rejected(self) -> None:
        import json

        path = self.directory / "build-provenance.json"
        data = json.loads(path.read_text())
        data["revision"] = "f" * 40
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            release.artifact_hashes(self.directory, "v0.1.0", self.sha)

    def test_upload_retry_preserves_identical_assets_and_uploads_only_missing_bytes(
        self,
    ) -> None:
        self.draft["assets"] = [{"name": self.archive.name}]

        def download(command: list[str], **kwargs: Any) -> None:
            if command[2] == "download":
                directory = Path(command[command.index("--dir") + 1])
                (directory / self.archive.name).write_bytes(self.archive.read_bytes())

        with (
            patch.object(release, "resolve", return_value=(self.draft, self.sha)),
            patch.object(subprocess, "run", side_effect=download) as run,
        ):
            release.upload(self.directory, "example/toolkit", "v0.1.0", self.sha)
        upload = run.call_args_list[-1].args[0]
        self.assertEqual(["gh", "release", "upload", "v0.1.0"], upload[:4])
        self.assertIn(str(self.directory / "SHA256SUMS"), upload)
        self.assertNotIn(str(self.archive), upload)
        self.assertNotIn("--clobber", upload)

    def test_complete_retry_does_not_upload_and_conflicts_never_overwrite(self) -> None:
        self.draft["assets"] = [
            {"name": name}
            for name in [
                self.archive.name,
                "jellyfin_plugin_toolkit-0.1.0.tar.gz",
                "SHA256SUMS",
                "build-provenance.json",
            ]
        ]
        for corrupt in [False, True]:
            with self.subTest(corrupt=corrupt):

                def download(
                    command: list[str], corrupt: bool = corrupt, **kwargs: Any
                ) -> None:
                    name = command[command.index("--pattern") + 1]
                    target = Path(command[command.index("--dir") + 1]) / name
                    target.write_bytes(
                        b"conflict" if corrupt else (self.directory / name).read_bytes()
                    )

                with (
                    patch.object(
                        release, "resolve", return_value=(self.draft, self.sha)
                    ),
                    patch.object(subprocess, "run", side_effect=download) as run,
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

    def test_publication_during_verification_stops_upload(self) -> None:
        with (
            patch.object(
                release,
                "resolve",
                side_effect=[(self.draft, self.sha), ValueError("published")],
            ),
            patch.object(subprocess, "run") as run,
            self.assertRaisesRegex(ValueError, "published"),
        ):
            release.upload(self.directory, "example/toolkit", "v0.1.0", self.sha)
        run.assert_not_called()
