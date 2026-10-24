"""Release locking rejects untrusted configuration before running uv or writing."""

import base64
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jellyfin_plugin_toolkit import release_lock


class ReleaseLockTests(unittest.TestCase):
    def test_changed_dependency_rejected_without_resolving_or_writing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            trusted = '[project]\nname="jellyfin-plugin-toolkit"\nversion = "0.2.0"\ndependencies=[]\n'
            (root / "pyproject.toml").write_text(trusted)
            injected = trusted.replace('"0.2.0"', '"0.3.0"').replace(
                "dependencies=[]", 'dependencies=["untrusted"]'
            )
            repo: dict[str, Any] = {"full_name": "example/toolkit"}
            pr: dict[str, Any] = {
                "state": "open",
                "number": 1,
                "user": {"login": "release[bot]", "type": "Bot"},
                "base": {"sha": "a" * 40, "repo": repo},
                "head": {"sha": "b" * 40, "ref": "release", "repo": repo},
            }
            with (
                patch.object(
                    release_lock,
                    "api",
                    side_effect=[
                        pr,
                        {"content": base64.b64encode(injected.encode()).decode()},
                    ],
                ) as api,
                patch.object(
                    subprocess,
                    "check_output",
                    return_value=("a" * 40).encode(),
                ),
                patch.object(subprocess, "run") as run,
            ):
                with self.assertRaisesRegex(ValueError, "beyond package version"):
                    release_lock.refresh(root, "example/toolkit", 1, "release[bot]")
            run.assert_not_called()
            self.assertEqual(2, api.call_count)

    def test_native_lock_refresh_commits_only_lock_with_fast_forward(self) -> None:
        import tomllib

        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=source / ".build") as folder:
            root = Path(folder)
            for name in ["pyproject.toml", "uv.lock", ".python-version"]:
                (root / name).write_bytes((source / name).read_bytes())
            trusted = (root / "pyproject.toml").read_text()
            current = tomllib.loads(trusted)["project"]["version"]
            major, minor, _ = map(int, current.split("."))
            selected = f"{major}.{minor + 1}.0"
            proposed = trusted.replace(
                f'version = "{current}"', f'version = "{selected}"', 1
            )
            repo: dict[str, Any] = {"full_name": "example/toolkit"}
            pr: dict[str, Any] = {
                "state": "open",
                "number": 1,
                "user": {"login": "release[bot]", "type": "Bot"},
                "base": {"sha": "a" * 40, "repo": repo},
                "head": {"sha": "b" * 40, "ref": "release", "repo": repo},
            }
            writes = []

            def api(
                repository: str,
                endpoint: str,
                method: str = "GET",
                body: dict[str, Any] | None = None,
            ) -> Any:
                if endpoint == "pulls/1":
                    return pr
                if endpoint.startswith("contents/"):
                    payload = (
                        proposed.encode()
                        if "pyproject.toml" in endpoint
                        else (root / "uv.lock").read_bytes()
                    )
                    return {"content": base64.b64encode(payload).decode()}
                if method != "GET":
                    assert body is not None
                    writes.append((endpoint, body))
                    return {"sha": "c" * 40}
                return {"tree": {"sha": "d" * 40}}

            with (
                patch.object(release_lock, "api", side_effect=api),
                patch.object(
                    release_lock,
                    "commit_identity",
                    return_value={
                        "name": "release[bot]",
                        "email": "123+release[bot]@users.noreply.github.com",
                    },
                ),
                patch.object(
                    subprocess,
                    "check_output",
                    return_value=("a" * 40).encode(),
                ),
            ):
                release_lock.refresh(root, "example/toolkit", 1, "release[bot]")
            updated = tomllib.loads(base64.b64decode(writes[0][1]["content"]).decode())
            self.assertEqual(
                selected,
                next(
                    p["version"]
                    for p in updated["package"]
                    if p["name"] == "jellyfin-plugin-toolkit"
                ),
            )
            self.assertEqual(
                ["uv.lock"], [item["path"] for item in writes[1][1]["tree"]]
            )
            self.assertEqual(writes[2][1]["author"], writes[2][1]["committer"])
            self.assertEqual("release[bot]", writes[2][1]["author"]["name"])
            self.assertFalse(writes[-1][1]["force"])
            self.assertEqual(trusted, (root / "pyproject.toml").read_text())
