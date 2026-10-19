"""Generic release identity, integrity and immutable upload recovery regressions."""

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from consumer_fixture import config

import jellyfin_plugin_toolkit.release.__main__ as release
from jellyfin_plugin_toolkit.common.release_metadata import (
    changelog_for_version,
    load_release,
)
from jellyfin_plugin_toolkit.scaffold import toml

REPOSITORY = "example/plugin"


class ReleaseTests(unittest.TestCase):
    def fixture(self, root):
        selected = copy.deepcopy(config)
        selected["distribution"]["web"] = True
        selected["backend"]["project_assemblies"] = ["Example.Core"]
        (root / "plugin.toml").write_text(toml(selected))
        (root / "version.txt").write_text("0.1.0\n")
        (root / "CHANGELOG.md").write_text(
            "## 0.1.0 (2026-10-19)\n\nFixture release.\n"
        )
        (root / "Directory.Build.props").write_text(
            "<Project><PropertyGroup><PluginVersion>0.1.0</PluginVersion></PropertyGroup></Project>"
        )
        path = root / "src/Host/packages.lock.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "dependencies": {
                        "net10.0": {
                            "Example.Host": {
                                "type": "Project",
                                "dependencies": {"Example.Core": "[0.1.0, )"},
                            }
                        }
                    }
                }
            )
        )

    def artifacts(self, root: Path):
        directory = root / "dist"
        directory.mkdir()
        metadata = load_release(root)
        tag = "v" + metadata.version
        binary = f"example-{metadata.version}.zip"
        for name in (
            binary,
            f"example-web-{metadata.version}.zip",
            f"example-source-{metadata.version}.zip",
        ):
            (directory / name).write_bytes(("synthetic fixture " + name).encode())
        catalog = [
            {
                "guid": metadata.guid,
                "versions": [
                    {
                        "version": metadata.jellyfin_version,
                        "targetAbi": metadata.abi,
                        "sourceUrl": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{binary}",
                        "checksum": hashlib.md5(
                            (directory / binary).read_bytes()
                        ).hexdigest(),
                    }
                ],
            }
        ]
        (directory / "manifest.json").write_text(json.dumps(catalog))
        (directory / "SHA256SUMS").write_text(
            "\n".join(
                hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name
                for p in sorted(directory.iterdir())
            )
            + "\n"
        )
        (directory / "validation.json").write_text(
            json.dumps(
                {
                    "repository": REPOSITORY,
                    "artifacts": release.artifact_hashes(
                        root, directory, REPOSITORY, tag
                    ),
                }
            )
        )
        return directory, tag

    def test_matching_existing_draft_resumes_without_creating_or_moving_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            tag = "v" + load_release(root).version
            output = root / "outputs"
            with (
                patch.object(
                    release,
                    "github_json",
                    side_effect=[
                        [{"tag_name": tag, "draft": True}],
                        {"object": {"type": "commit", "sha": "same"}},
                    ],
                ),
                patch.object(release.subprocess, "check_output", return_value="same\n"),
            ):
                release.resume_draft(root, REPOSITORY, output)
            self.assertIn("release_created=true", output.read_text())
            with (
                patch.object(
                    release,
                    "github_json",
                    side_effect=[
                        [{"tag_name": tag, "draft": True}],
                        {"object": {"type": "commit", "sha": "moved"}},
                    ],
                ),
                patch.object(release.subprocess, "check_output", return_value="same\n"),
                self.assertRaisesRegex(ValueError, "does not match"),
            ):
                release.resume_draft(root, REPOSITORY, output)
            with (
                patch.object(
                    release,
                    "github_json",
                    return_value=[{"tag_name": tag, "draft": True}] * 2,
                ),
                self.assertRaisesRegex(ValueError, "duplicate"),
            ):
                release.resume_draft(root, REPOSITORY, output)

    def test_identity_changelog_and_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            tag = release.check_identity(root)
            env = root / "environment"
            release.prepare(root, REPOSITORY, tag, env)
            self.assertIn(
                "https://github.com/" + REPOSITORY + "/releases/download/" + tag,
                env.read_text(),
            )
            with self.assertRaisesRegex(ValueError, "tag"):
                release.check_identity(root, "v99.0.0")
            (root / "version.txt").write_text("99.0.0")
            with self.assertRaisesRegex(ValueError, "Conflicting version"):
                release.check_identity(root)

    def test_changelog_extracts_only_matching_release_please_section(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [0.1.1](https://github.com/example/repo/compare/v0.1.0...v0.1.1) (2026-10-12)\n\n### Fixes\n\n* Current notes\n\n## 0.1.0\n\nOld notes\n"
            )
            self.assertEqual(
                changelog_for_version(root, "0.1.1"), "### Fixes\n\n* Current notes"
            )
            with self.assertRaisesRegex(ValueError, "Missing changelog"):
                changelog_for_version(root, "0.1.2")

    def test_stale_lock_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            lock = root / "src/Host/packages.lock.json"
            lock.write_text(
                lock.read_text().replace(
                    "[" + load_release(root).version + ", )", "[99.0.0, )"
                )
            )
            with self.assertRaisesRegex(ValueError, "stale"):
                release.check_identity(root)

    def test_checksums_and_public_url_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            directory, tag = self.artifacts(root)
            release.artifact_hashes(root, directory, REPOSITORY, tag)
            with self.assertRaisesRegex(ValueError, "URL"):
                release.artifact_hashes(root, directory, "other/repository", tag)
            (directory / f"example-{load_release(root).version}.zip").write_bytes(
                b"corruption"
            )
            with self.assertRaisesRegex(ValueError, "checksum"):
                release.artifact_hashes(root, directory, REPOSITORY, tag)

    def test_draft_upload_retries_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            directory, tag = self.artifacts(root)
            hashes = release.artifact_hashes(root, directory, REPOSITORY, tag)
            draft = {
                "draft": True,
                "tag_name": tag,
                "assets": [{"name": name} for name in [*hashes, "validation.json"]],
            }

            def download(command, **kwargs):
                name = command[command.index("--pattern") + 1]
                target = Path(command[command.index("--dir") + 1]) / name
                shutil.copyfile(directory / name, target)
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(
                    release,
                    "github_json",
                    side_effect=[
                        [draft],
                        {"object": {"type": "commit", "sha": "fixture"}},
                    ],
                ),
                patch.object(
                    release.subprocess, "check_output", return_value="fixture\n"
                ),
                patch.object(release.subprocess, "run", side_effect=download) as run,
            ):
                release.upload(root, directory, REPOSITORY, tag)
                self.assertTrue(
                    all(call.args[0][2] == "download" for call in run.call_args_list)
                )
            conflict = copy.deepcopy(draft)

            def corrupted_download(command, **kwargs):
                name = command[command.index("--pattern") + 1]
                (Path(command[command.index("--dir") + 1]) / name).write_bytes(
                    b"conflict"
                )
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(
                    release,
                    "github_json",
                    side_effect=[
                        [conflict],
                        {"object": {"type": "commit", "sha": "fixture"}},
                    ],
                ),
                patch.object(
                    release.subprocess, "check_output", return_value="fixture\n"
                ),
                patch.object(release.subprocess, "run", side_effect=corrupted_download),
                self.assertRaisesRegex(ValueError, "Conflicting"),
            ):
                release.upload(root, directory, REPOSITORY, tag)

    def test_public_receipt_mismatch_stops_before_remote_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            directory, tag = self.artifacts(root)
            receipt = json.loads((directory / "validation.json").read_text())
            receipt["artifacts"]["SHA256SUMS"] = "0" * 64
            (directory / "validation.json").write_text(json.dumps(receipt))
            with (
                patch.object(release, "github_json") as remote,
                self.assertRaisesRegex(ValueError, "receipt"),
            ):
                release.upload(root, directory, REPOSITORY, tag)
            remote.assert_not_called()

    def test_initial_upload_attaches_only_required_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            directory, tag = self.artifacts(root)
            with (
                patch.object(
                    release,
                    "github_json",
                    side_effect=[
                        [{"draft": True, "tag_name": tag}],
                        {"object": {"type": "commit", "sha": "tested"}},
                    ],
                ),
                patch.object(
                    release.subprocess, "check_output", return_value="tested\n"
                ),
                patch.object(release.subprocess, "run") as run,
            ):
                release.upload(root, directory, REPOSITORY, tag)
                command = run.call_args.args[0]
                self.assertEqual(command[:4], ["gh", "release", "upload", tag])
                self.assertNotIn("--clobber", command)
                self.assertNotIn("--draft=false", command)
                self.assertEqual(len(command[4:-2]), 6)

    def test_missing_release_date_fails_before_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CHANGELOG.md").write_text("## 0.1.0\n\nUndated notes\n")
            with self.assertRaisesRegex(ValueError, "release date"):
                changelog_for_version(root, "0.1.0")

    def test_published_release_and_moved_tag_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            directory, tag = self.artifacts(root)
            with (
                patch.object(
                    release,
                    "github_json",
                    return_value=[{"draft": False, "tag_name": tag}],
                ),
                self.assertRaisesRegex(ValueError, "draft"),
            ):
                release.upload(root, directory, REPOSITORY, tag)
            with (
                patch.object(
                    release,
                    "github_json",
                    side_effect=[
                        [{"draft": True, "tag_name": tag}],
                        {"object": {"type": "commit", "sha": "changed"}},
                    ],
                ),
                patch.object(
                    release.subprocess, "check_output", return_value="tested\n"
                ),
                self.assertRaisesRegex(ValueError, "moved"),
            ):
                release.upload(root, directory, REPOSITORY, tag)
