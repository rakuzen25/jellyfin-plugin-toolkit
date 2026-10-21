"""Exercise a generated consumer's real, owned development sandbox."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def exercise_edits(root, log, child, base):
    """Observe actual filesystem events, failed compilation and backend deployment."""

    def wait_for(marker, offset):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Development supervisor exited during edits")
            if marker in log.read_text()[offset:]:
                return
            time.sleep(0.25)
        raise TimeoutError("Development edit did not produce " + marker)

    config = json.loads((root / ".build/generated/plugin.json").read_text())
    frontend = config.get("frontend", {})
    if frontend.get("enabled"):
        entry = frontend.get("admin_entry") or next(iter(frontend["entries"]))
        source = root / frontend.get("directory", "frontend") / entry
        original = source.read_bytes()
        try:
            offset = len(log.read_text())
            source.write_bytes(original + b"\nconst = ;\n")
            wait_for("save again or press r to retry", offset)
            with urllib.request.urlopen(base + "/health", timeout=5) as response:
                assert response.status == 200
            offset = len(log.read_text())
            source.write_bytes(original)
            wait_for("[dev] Ready ", offset)
        finally:
            if source.read_bytes() != original:
                source.write_bytes(original)
    source = next(
        path
        for path in (root / config["backend"]["entry"]).parent.rglob("*.cs")
        if not {"obj", "bin"} & set(path.relative_to(root).parts)
    )
    original = source.read_bytes()
    try:
        offset = len(log.read_text())
        source.write_bytes(original + b"\n// Exercise development restart.\n")
        wait_for("[dev] Ready ", offset)
        assert "Building backend" in log.read_text()[offset:]
        with urllib.request.urlopen(base + "/health", timeout=5) as response:
            assert response.status == 200
    finally:
        source.write_bytes(original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("consumer", type=Path)
    parser.add_argument("--edits", action="store_true")
    args = parser.parse_args()
    root = args.consumer.resolve()
    from jellyfin_plugin_toolkit.resources_api import RESOURCE_ROOT

    toolkit = RESOURCE_ROOT
    env = {
        **os.environ,
        "JELLYFIN_PLUGIN_ROOT": str(root),
        "JELLYFIN_PLUGIN_TOOLKIT_ROOT": str(toolkit),
        "JELLYFIN_PLUGIN_DEV_PYTHON": sys.executable,
    }
    with socket.socket() as address:
        address.bind(("127.0.0.1", 0))
        port = address.getsockname()[1]
    log = root / ".build/dev-smoke.log"
    with log.open("w") as output:
        child = subprocess.Popen(
            [
                "node",
                str(toolkit / "jellyfin_plugin_toolkit/dev/session.mjs"),
                "--port",
                str(port),
                *([] if args.edits else ["--no-watch"]),
            ],
            cwd=root,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("Development supervisor exited before readiness")
                if "[dev] Ready " in log.read_text():
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=5
                    ) as response:
                        assert response.status == 200
                    break
                time.sleep(0.25)
            else:
                raise TimeoutError("Owned development sandbox did not become ready")
            if args.edits:
                exercise_edits(root, log, child, f"http://127.0.0.1:{port}")
        except BaseException:
            print(log.read_text(), file=sys.stderr)
            raise
        finally:
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                raise RuntimeError("Development supervisor failed to stop")
            finally:
                # The sandbox command verifies checkout ownership before removing anything.
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "jellyfin_plugin_toolkit.dev.local_jellyfin",
                        "stop",
                    ],
                    cwd=root,
                    env=env,
                    check=True,
                )
    assert child.returncode == 0, log.read_text()
    assert "[dev] Stopped; sandbox data retained." in log.read_text()
    print("PASS: owned development sandbox starts, serves health, and stops cleanly")
    if args.edits:
        print("PASS: watched frontend failure/recovery and backend restart")


if __name__ == "__main__":
    main()
