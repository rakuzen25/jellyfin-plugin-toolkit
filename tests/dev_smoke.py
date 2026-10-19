"""Exercise a generated consumer's real, owned development sandbox."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("consumer", type=Path)
    root = parser.parse_args().consumer.resolve()
    toolkit = (root / ".build/toolkit/current").resolve()
    env = {
        **os.environ,
        "JELLYFIN_PLUGIN_ROOT": str(root),
        "JELLYFIN_PLUGIN_TOOLKIT_ROOT": str(toolkit),
        "PYTHONPATH": str(toolkit) + os.pathsep + str(root),
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
                "--no-watch",
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


if __name__ == "__main__":
    main()
