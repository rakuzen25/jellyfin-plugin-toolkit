"""Owned Docker sandbox operations used by the persistent Node development session."""

import argparse
import fcntl
import hashlib
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from jellyfin_plugin_toolkit.build.backend import publish
from jellyfin_plugin_toolkit.common.process import IMAGES, ROOT, run
from jellyfin_plugin_toolkit.common.release_metadata import load_identity
from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT, Context

CONTEXT = Context.load()

REPOSITORY = ROOT
DATA = ROOT / ".build/local-jellyfin"
OWNER_LABEL = "jellyfin-plugin-toolkit.local-checkout"
OWNER = str(ROOT)
CONTAINER = CONTEXT.prefix + "-local-" + hashlib.sha256(OWNER.encode()).hexdigest()[:10]


def inspect() -> dict | None:
    # Listing avoids treating daemon/permission errors as an absent container.
    result = subprocess.check_output(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=^/" + CONTAINER + "$",
            "--format",
            "{{.ID}}",
        ],
        text=True,
    ).strip()
    if not result:
        return None
    container = json.loads(
        subprocess.check_output(["docker", "inspect", CONTAINER], text=True)
    )[0]
    if container["Config"].get("Labels", {}).get(OWNER_LABEL) != OWNER:
        raise SystemExit(
            "Container name belongs to another installation; refusing to change it."
        )
    return container


def media_paths(requested: list[str] | None) -> list[str]:
    settings = DATA / "media-mounts.json"
    values = (
        requested
        if requested is not None
        else json.loads(settings.read_text())
        if settings.exists()
        else []
    )
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        raise SystemExit(
            "Invalid saved media mounts. Use --clear-media-mounts to clear them."
        )
    paths: list[str] = []
    for value in values:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise SystemExit(
                "--media-mount requires an absolute directory path: " + value
            )
        # Preserve the host path used by absolute symlinks, rather than resolving
        # it to a different target. Normalize only lexical path components.
        path = Path(os.path.abspath(path))
        if not path.is_dir():
            raise SystemExit("Media mount directory not found: " + str(path))
        if (
            "," in str(path)
            or path == Path("/")
            or any(
                path == base or base in path.parents
                for base in (Path("/config"), Path("/cache"), Path("/media"))
            )
        ):
            raise SystemExit(
                "Media mount conflicts with sandbox paths or Docker mount syntax: "
                + str(path)
            )
        if str(path) not in paths:
            paths.append(str(path))
    return paths


def stop() -> None:
    if inspect():
        run("docker", "rm", "-f", CONTAINER)
    (DATA / "session.json").unlink(missing_ok=True)


def wait_ready(port: int) -> None:
    for _ in range(60):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=2
            ) as response:
                if response.status == 200 and response.read().strip() == b"Healthy":
                    return
        except OSError, http.client.HTTPException:
            pass
        time.sleep(1)
    run("docker", "logs", "--tail", "60", CONTAINER)
    raise RuntimeError("Jellyfin startup timed out")


def start(mounts: list[str]) -> int:
    for name in ("config", "cache", "media"):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    run(
        "docker",
        "run",
        "-d",
        "--platform",
        IMAGES["platform"],
        "--name",
        CONTAINER,
        "--label",
        OWNER_LABEL + "=" + OWNER,
        "-p",
        "127.0.0.1::8096",
        "-v",
        f"{DATA / 'config'}:/config",
        "-v",
        f"{DATA / 'cache'}:/cache",
        "-v",
        f"{DATA / 'media'}:/media:ro",
        *(
            argument
            for path in mounts
            for argument in ("--mount", f"type=bind,src={path},dst={path},readonly")
        ),
        IMAGES["jellyfin"],
    )
    container = inspect()
    assert container is not None
    port = int(container["NetworkSettings"]["Ports"]["8096/tcp"][0]["HostPort"])
    wait_ready(port)
    (DATA / "session.json").write_text(json.dumps({"port": port}) + "\n")
    return port


def deploy(web: Path, requested_mounts: list[str] | None = None) -> None:
    mounts = media_paths(requested_mounts)
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    release = load_identity()
    plugins = DATA / "config/plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    previous = list(plugins.glob(CONTEXT.identity["name"] + "_*"))
    for folder in previous:
        meta = folder / "meta.json"
        if (
            not meta.is_file()
            or json.loads(meta.read_text()).get("guid") != release.guid
        ):
            raise ValueError("Unexpected plugin folder: " + str(folder))
    # Compile/validate before taking down the currently usable installation.
    with tempfile.TemporaryDirectory(prefix="plugin-", dir=DATA) as temporary:
        staging = Path(temporary)
        candidate = staging / "candidate"
        publish(candidate, web, configuration="Debug")
        if not (candidate / (CONTEXT.identity["assembly"] + ".dll")).is_file():
            raise ValueError("Build did not produce the plugin assembly")
        (candidate / "meta.json").write_text(
            json.dumps(
                {
                    "guid": release.guid,
                    "name": release.name,
                    "description": release.description,
                    "version": release.jellyfin_version,
                    "targetAbi": release.abi,
                    "status": "Active",
                    "autoUpdate": False,
                    "assemblies": [p.name for p in sorted(candidate.glob("*.dll"))],
                },
                indent=2,
            )
            + "\n"
        )
        backup = staging / "previous"
        backup.mkdir()
        for folder in previous:
            shutil.copytree(folder, backup / folder.name)
        was_running = bool(inspect())
        stop()
        target = plugins / (CONTEXT.identity["name"] + "_" + release.jellyfin_version)
        try:
            for folder in previous:
                shutil.rmtree(folder)
            shutil.copytree(candidate, target)
            start(mounts)
        except BaseException:
            stop()
            if target.exists():
                shutil.rmtree(target)
            for folder in backup.iterdir():
                shutil.copytree(folder, plugins / folder.name)
            if was_running and previous:
                print("Replacement failed; restoring the previous plugin", flush=True)
                start(mounts)
            raise
    (DATA / "media-mounts.json").write_text(json.dumps(mounts, indent=2) + "\n")


def dev(args: argparse.Namespace) -> None:
    from jellyfin_plugin_toolkit.tasks import verify_tools

    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (DATA / "session.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "A development session already owns this checkout"
            ) from None
        media_paths([] if args.clear_media_mounts else args.media_mount)
        verify_tools()
        if (
            CONTEXT.config.get("frontend", {}).get("enabled", False)
            and not (
                ROOT
                / CONTEXT.config["frontend"].get("directory", "frontend")
                / "node_modules/esbuild"
            ).exists()
        ):
            raise ValueError(
                "Workspace dependencies are missing; run pnpm install --frozen-lockfile"
            )
        from jellyfin_plugin_toolkit.dev.references import ensure

        ensure()
        command = [
            "node",
            str(TOOLKIT_ROOT / "jellyfin_plugin_toolkit/dev/session.mjs"),
            "--port",
            str(args.port),
        ]
        if args.no_watch:
            command.append("--no-watch")
        if args.open:
            command.append("--open")
        if args.clear_media_mounts:
            command.append("--clear-media-mounts")
        for mount in args.media_mount or []:
            command.extend(["--media-mount", mount])
        environment = {
            **os.environ,
            "JELLYFIN_PLUGIN_DEV_PYTHON": sys.executable,
            "PYTHONPATH": str(TOOLKIT_ROOT),
        }
        child = subprocess.Popen(command, cwd=ROOT, env=environment)
        previous_handler = signal.signal(
            signal.SIGTERM, lambda signum, frame: child.send_signal(signal.SIGTERM)
        )
        try:
            code = child.wait()
            if code:
                raise subprocess.CalledProcessError(code, command)
        except KeyboardInterrupt:
            child.send_signal(signal.SIGINT)
            child.wait()
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
            if child.poll() is None:
                child.terminate()
                child.wait()
            stop()


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=["deploy", "stop"])
    parser.add_argument("--web", type=Path)
    parser.add_argument("--media-mount", action="append")
    parser.add_argument("--clear-media-mounts", action="store_true")
    args = parser.parse_args()
    if args.operation == "stop":
        stop()
    elif args.web:
        deploy(args.web.resolve(), [] if args.clear_media_mounts else args.media_mount)
    else:
        parser.error("deploy requires --web")


if __name__ == "__main__":
    main()
