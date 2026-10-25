"""Exercise uv's native release-age policy against timestamped index artifacts."""

import hashlib
import io
import json
import subprocess
import tempfile
import threading
import tomllib
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class NativeAgePolicyTests(unittest.TestCase):
    def test_uv_excludes_fresh_release_and_rejects_required_fresh_version(self) -> None:
        files: dict[str, Any] = {}
        for version in ["0.1.0", "1.0.0"]:
            stream = io.BytesIO()
            prefix = f"jpt_age_probe-{version}.dist-info/"
            with zipfile.ZipFile(stream, "w") as wheel:
                wheel.writestr(
                    prefix + "METADATA",
                    f"Metadata-Version: 2.3\nName: jpt-age-probe\nVersion: {version}\n",
                )
                wheel.writestr(
                    prefix + "WHEEL",
                    "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                )
                wheel.writestr(prefix + "RECORD", "")
            files[f"jpt_age_probe-{version}-py3-none-any.whl"] = stream.getvalue()
        now = datetime.now(UTC)
        listing: dict[str, Any] = {
            "meta": {"api-version": "1.1"},
            "name": "jpt-age-probe",
            "files": [
                {
                    "filename": name,
                    "url": "/" + name,
                    "hashes": {"sha256": hashlib.sha256(payload).hexdigest()},
                    "upload-time": (
                        now
                        - (
                            timedelta(days=2)
                            if "0.1.0" in name
                            else timedelta(minutes=5)
                        )
                    ).isoformat(),
                    "size": len(payload),
                }
                for name, payload in files.items()
            ],
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_HEAD(self) -> None:
                self.do_GET()

            def do_GET(self) -> None:
                filename = self.path.lstrip("/")
                package = self.path == "/simple/jpt-age-probe/"
                payload = (
                    json.dumps(listing).encode() if package else files.get(filename)
                )
                if payload is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/vnd.pypi.simple.v1+json"
                    if package
                    else "application/octet-stream",
                )
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        source = Path(__file__).resolve().parents[1]
        try:
            with tempfile.TemporaryDirectory(dir=source / ".build") as folder:
                root = Path(folder)
                subprocess.run(
                    [
                        "uv",
                        "init",
                        "--bare",
                        "--no-package",
                        "--no-workspace",
                        "--vcs",
                        "none",
                        "--name",
                        "age-fixture",
                        "--python",
                        (source / ".python-version").read_text().strip(),
                        str(root),
                    ],
                    check=True,
                    capture_output=True,
                )
                settings = tomllib.loads((source / "pyproject.toml").read_text())[
                    "tool"
                ]["uv"]
                with (root / "pyproject.toml").open("a") as settings_output:
                    settings_output.write(
                        "\n[tool.uv]\n"
                        + "\n".join(
                            key + " = " + json.dumps(settings[key])
                            for key in [
                                "required-version",
                                "python-preference",
                                "exclude-newer",
                            ]
                        )
                        + "\n"
                    )
                index = f"http://127.0.0.1:{server.server_port}/simple"
                resolved = subprocess.run(
                    [
                        "uv",
                        "add",
                        "--no-sync",
                        "--default-index",
                        index,
                        "jpt-age-probe>=0.1",
                    ],
                    cwd=root,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, resolved.returncode, resolved.stderr.decode())
                before = (root / "uv.lock").read_bytes()
                locked = tomllib.loads(before.decode())
                self.assertEqual(
                    "0.1.0",
                    next(
                        item["version"]
                        for item in locked["package"]
                        if item["name"] == "jpt-age-probe"
                    ),
                )
                result = subprocess.run(
                    ["uv", "add", "--no-sync", "jpt-age-probe>=1"],
                    cwd=root,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(before, (root / "uv.lock").read_bytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
