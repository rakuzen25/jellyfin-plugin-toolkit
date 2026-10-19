"""Sandbox replacement, rollback, mount, and ownership behavior without Docker."""

import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from jellyfin_plugin_toolkit.dev import local_jellyfin as sandbox
from jellyfin_plugin_toolkit.dev.references import inventory, verify


class LocalSandboxTests(unittest.TestCase):
    def test_dev_forwards_no_watch_to_supervisor(self):
        for no_watch in (False, True):
            with (
                self.subTest(no_watch=no_watch),
                tempfile.TemporaryDirectory() as directory,
            ):
                child = MagicMock()
                child.wait.return_value = 0
                child.poll.return_value = 0
                with (
                    patch.object(sandbox, "DATA", Path(directory)),
                    patch.object(sandbox, "media_paths"),
                    patch("jellyfin_plugin_toolkit.tasks.verify_tools"),
                    patch("jellyfin_plugin_toolkit.dev.references.ensure"),
                    patch("pathlib.Path.exists", return_value=True),
                    patch.object(
                        sandbox.subprocess, "Popen", return_value=child
                    ) as spawn,
                    patch.object(sandbox, "stop"),
                ):
                    sandbox.dev(
                        argparse.Namespace(
                            no_watch=no_watch,
                            open=False,
                            port=18096,
                            clear_media_mounts=False,
                            media_mount=None,
                        )
                    )
                self.assertEqual(no_watch, "--no-watch" in spawn.call_args.args[0])

    def test_readiness_waits_for_healthy_body_and_retries_startup_connection_resets(
        self,
    ):
        degraded, healthy = MagicMock(), MagicMock()
        degraded.__enter__.return_value.status = 200
        degraded.__enter__.return_value.read.return_value = b"Degraded"
        healthy.__enter__.return_value.status = 200
        healthy.__enter__.return_value.read.return_value = b"Healthy"
        with (
            patch.object(
                sandbox.urllib.request,
                "urlopen",
                side_effect=[degraded, ConnectionResetError("starting"), healthy],
            ) as request,
            patch.object(sandbox.time, "sleep") as sleep,
        ):
            sandbox.wait_ready(18096)
        self.assertEqual(3, request.call_count)
        self.assertEqual(2, sleep.call_count)

    def test_another_session_cannot_take_the_checkout_lock(self):
        import argparse
        import fcntl

        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            with (
                (data / "session.lock").open("w") as lock,
                patch.object(sandbox, "DATA", data),
                patch("jellyfin_plugin_toolkit.tasks.verify_tools") as tools,
            ):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, "already owns"):
                    sandbox.dev(argparse.Namespace())
                tools.assert_not_called()

    def test_media_mounts_are_deduplicated_and_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "sample music"
            media.mkdir()
            (root / "media-mounts.json").write_text(json.dumps([str(media)]))
            with patch.object(sandbox, "DATA", root):
                self.assertEqual([str(media)], sandbox.media_paths(None))
                self.assertEqual(
                    [str(media)], sandbox.media_paths([str(media), str(media)])
                )
                self.assertEqual([], sandbox.media_paths([]))
                for invalid in ["relative/path", "/", "/config"]:
                    with self.assertRaises(SystemExit):
                        sandbox.media_paths([invalid])

    def test_missing_media_fails_before_build(self):
        with patch.object(sandbox, "publish") as publish:
            with self.assertRaises(SystemExit):
                sandbox.deploy(Path("unused"), ["/this-directory-does-not-exist"])
            publish.assert_not_called()

    def test_foreign_container_is_never_adopted(self):
        with (
            patch.object(
                sandbox.subprocess,
                "check_output",
                side_effect=["id", json.dumps([{"Config": {"Labels": {}}}])],
            ),
            self.assertRaisesRegex(SystemExit, "another installation"),
        ):
            sandbox.inspect()

    def test_daemon_errors_are_not_treated_as_absence(self):
        with (
            patch.object(
                sandbox.subprocess,
                "check_output",
                side_effect=subprocess.CalledProcessError(1, "docker"),
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            sandbox.inspect()

    def replacement(self, *, build_fails=False, startup_fails=False):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            plugins = data / "config/plugins"
            previous = plugins / (sandbox.CONTEXT.identity["name"] + "_previous")
            previous.mkdir(parents=True)
            release = sandbox.load_identity()
            (previous / "meta.json").write_text(json.dumps({"guid": release.guid}))
            (previous / "old.dll").write_text("last working plugin")
            other = plugins / "OtherPlugin"
            other.mkdir()
            database = data / "config/test.db"
            database.write_text("retained")
            calls = []

            def publish(output, web, **kwargs):
                calls.append("build")
                if build_fails:
                    raise ValueError("compile error")
                output.mkdir()
                (output / (sandbox.CONTEXT.identity["assembly"] + ".dll")).write_text(
                    "new plugin"
                )

            def start(mounts):
                calls.append("start")
                if startup_fails and calls.count("start") == 1:
                    raise RuntimeError("startup error")

            with (
                patch.object(sandbox, "DATA", data),
                patch.object(sandbox, "publish", side_effect=publish),
                patch.object(sandbox, "inspect", return_value={"owned": True}),
                patch.object(sandbox, "stop", side_effect=lambda: calls.append("stop")),
                patch.object(sandbox, "start", side_effect=start),
            ):
                if build_fails or startup_fails:
                    with self.assertRaisesRegex((ValueError, RuntimeError), "error"):
                        sandbox.deploy(data / "web")
                    self.assertEqual(
                        "last working plugin", (previous / "old.dll").read_text()
                    )
                else:
                    sandbox.deploy(data / "web")
                    self.assertFalse(previous.exists())
                    self.assertTrue(
                        (
                            plugins
                            / (
                                sandbox.CONTEXT.identity["name"]
                                + "_"
                                + release.jellyfin_version
                            )
                            / (sandbox.CONTEXT.identity["assembly"] + ".dll")
                        ).exists()
                    )
            self.assertTrue(other.exists())
            self.assertEqual("retained", database.read_text())
            return calls

    def test_successful_replacement_builds_before_stopping_and_preserves_data(self):
        self.assertEqual(["build", "stop", "start"], self.replacement())

    def test_compile_failure_keeps_running_installation(self):
        self.assertEqual(["build"], self.replacement(build_fails=True))

    def test_startup_failure_rolls_back_and_restarts_previous_plugin(self):
        self.assertEqual(
            ["build", "stop", "start", "stop", "start"],
            self.replacement(startup_fails=True),
        )

    def test_reference_integrity_detects_corruption_and_image_changes(self):
        from jellyfin_plugin_toolkit.common.process import IMAGES

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "MediaBrowser.Common.dll",
                "MediaBrowser.Controller.dll",
                "MediaBrowser.Model.dll",
            ):
                (root / name).write_text("assembly")
            (root / "image.txt").write_text(IMAGES["jellyfin"])
            (root / "cache-integrity.json").write_text(json.dumps(inventory(root)))
            self.assertTrue(verify(root))
            (root / "MediaBrowser.Common.dll").write_text("changed")
            self.assertFalse(verify(root))
            (root / "image.txt").write_text("other image")
            self.assertFalse(verify(root))
