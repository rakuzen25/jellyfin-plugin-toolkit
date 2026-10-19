"""Disposable installation fixtures with no caller-supplied server or container targets."""

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
import zipfile
from pathlib import Path

from jellyfin_plugin_toolkit.acceptance import inventory, members
from jellyfin_plugin_toolkit.context import Context

OWNER_LABEL = "jellyfin-plugin-toolkit.acceptance-owner"


class Session:
    def __init__(self, archive: Path | None = None):
        self.context = Context.load()
        self.name = "plugin-acceptance-" + uuid.uuid4().hex
        self.temporary = tempfile.TemporaryDirectory(prefix=self.name + "-")
        self.root = Path(self.temporary.name)
        self.archive = (
            archive
            or self.context.root
            / "dist"
            / f"{self.context.prefix}-{self.context.version}.zip"
        )
        self.base = ""
        self.token = ""
        self.container_id = None

    def docker(self, *args):
        return subprocess.check_output(
            ["docker", *args], text=True, stderr=subprocess.STDOUT
        ).strip()

    def owned(self):
        if not self.container_id:
            raise ValueError("No disposable container was created by this session")
        container = json.loads(self.docker("inspect", self.container_id))[0]
        if container["Config"].get("Labels", {}).get(OWNER_LABEL) != self.name:
            raise ValueError(
                "Disposable container ownership changed; refusing to manage it"
            )
        return container

    def endpoint(self):
        container = self.owned()
        port = container["NetworkSettings"]["Ports"]["8096/tcp"][0]["HostPort"]
        self.base = "http://127.0.0.1:" + port

    def call(self, path, method="GET", body=None):
        authorization = 'MediaBrowser Client="PluginAcceptance", Device="Disposable", DeviceId="fixture", Version="1"'
        if self.token:
            authorization += ', Token="' + self.token + '"'
        request = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            return json.loads(data) if data else None

    def diagnostics(self):
        if not self.container_id:
            return
        evidence = self.context.root / "dist/evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        try:
            (evidence / "installation-startup.log").write_text(
                self.docker("logs", self.container_id)
            )
        except subprocess.CalledProcessError:
            pass

    def ready(self):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                info = self.call("/System/Info/Public")
                version = info.get("Version", info.get("version", ""))
                if (
                    version.split(".")[:2]
                    == self.context.config["host"]["abi"].split(".")[:2]
                ):
                    with urllib.request.urlopen(
                        self.base + "/health", timeout=5
                    ) as response:
                        if response.read().strip() == b"Healthy":
                            return
            except urllib.error.URLError, TimeoutError, ConnectionError:
                pass
            if not self.owned()["State"]["Running"]:
                break
            time.sleep(0.5)
        self.diagnostics()
        raise RuntimeError(
            "Disposable Jellyfin did not become ready; inspect dist/evidence/installation-startup.log"
        )

    def restart(self):
        self.owned()
        self.docker("restart", "--timeout", "15", self.container_id)
        self.endpoint()  # Docker allocates a new ephemeral host port on restart.
        self.ready()

    def assert_loaded(self):
        plugins = self.call("/Plugins")
        expected = uuid.UUID(self.context.identity["guid"])
        plugin = next(
            (p for p in plugins if uuid.UUID(p.get("id", p.get("Id", ""))) == expected),
            None,
        )
        if plugin is None or plugin.get("status", plugin.get("Status")) != "Active":
            raise AssertionError("Packaged plugin did not load: " + str(plugin))

    def extract(self, archive, target):
        with zipfile.ZipFile(archive) as package:
            names = members(package)
            metadata = json.loads(package.read("meta.json"))
            if uuid.UUID(metadata["guid"]) != uuid.UUID(self.context.identity["guid"]):
                raise ValueError("Installation archive belongs to a different plugin")
            if self.context.identity["assembly"] + ".dll" not in names:
                raise ValueError("Installation archive is missing the host assembly")
            if sum(item.file_size for item in package.infolist()) > 1_000_000_000:
                raise ValueError("Expanded installation archive exceeds size limit")
            for member in package.infolist():
                if member.is_dir():
                    continue
                path = target / member.filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(package.read(member))

    def replace(self, archive):
        """Install verified candidate bytes into this fixture, retaining configuration."""
        with tempfile.TemporaryDirectory(
            prefix="replacement-", dir=self.root
        ) as directory:
            staged = Path(directory) / "payload"
            self.extract(
                archive, staged
            )  # Validate before interrupting the working server.
            self.owned()
            self.docker("stop", "--timeout", "15", self.container_id)
            plugins = self.root / "config/plugins"
            backup = Path(directory) / "previous"
            backup.mkdir()
            expected = uuid.UUID(self.context.identity["guid"])
            for folder in plugins.iterdir():
                meta = folder / "meta.json"
                if (
                    meta.is_file()
                    and uuid.UUID(json.loads(meta.read_text())["guid"]) == expected
                ):
                    folder.rename(backup / folder.name)
            target = plugins / self.context.prefix
            installed = False
            try:
                if target.exists():
                    raise ValueError(
                        "Replacement directory belongs to a different plugin"
                    )
                staged.rename(target)
                installed = True
                self.docker("start", self.container_id)
                self.endpoint()
                self.ready()
                self.assert_loaded()
            except BaseException:
                self.owned()
                self.docker("stop", "--timeout", "15", self.container_id)
                if installed and target.exists():
                    shutil.rmtree(target)
                for folder in backup.iterdir():
                    folder.rename(plugins / folder.name)
                self.docker("start", self.container_id)
                self.endpoint()
                self.ready()
                raise

    def populate_media(self, media):
        """Plugin adapters may add synthetic fixtures before the read-only mount."""
        with wave.open(str(media / "track.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(bytes(32000))

    def media_inventory(self):
        media = self.root / "media"
        return {
            path.relative_to(media).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in media.rglob("*")
            if path.is_file()
        }

    def __enter__(self):
        try:
            config = self.root / "config"
            config.mkdir()
            media = self.root / "media"
            media.mkdir()
            self.populate_media(media)
            self.media_hashes = self.media_inventory()
            self.extract(self.archive, config / "plugins" / self.context.prefix)
            result = self.docker(
                "create",
                "--platform",
                "linux/amd64",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--name",
                self.name,
                "--label",
                OWNER_LABEL + "=" + self.name,
                "-p",
                "127.0.0.1::8096",
                "--mount",
                f"type=bind,source={config},target=/config",
                "--tmpfs",
                "/cache:rw,size=3g,mode=1777",
                "--mount",
                f"type=bind,source={media},target=/fixture-media,readonly",
                self.context.config["host"]["image"],
            )
            identifier = result.splitlines()[-1]
            if len(identifier) != 64 or any(
                c not in "0123456789abcdef" for c in identifier
            ):
                raise ValueError("Docker did not return an owned container identifier")
            self.container_id = identifier
            self.owned()
            self.docker("start", self.container_id)
            self.endpoint()
            self.ready()
            self.call(
                "/Startup/Configuration",
                "POST",
                {
                    "UICulture": "en-US",
                    "MetadataCountryCode": "US",
                    "PreferredMetadataLanguage": "en",
                },
            )
            password = secrets.token_urlsafe(24)
            self.call("/Startup/User")
            self.call(
                "/Startup/User", "POST", {"Name": "fixture-admin", "Password": password}
            )
            self.call("/Startup/Complete", "POST", {})
            authentication = self.call(
                "/Users/AuthenticateByName",
                "POST",
                {"Username": "fixture-admin", "Pw": password},
            )
            self.user = authentication.get("User", authentication.get("user", {}))
            self.token = authentication.get(
                "accessToken", authentication.get("AccessToken", "")
            )
            if not self.token:
                raise AssertionError("Disposable administrator authentication failed")
            self.assert_loaded()
            return self
        except BaseException:
            self.diagnostics()
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *exception):
        try:
            if exception and exception[0]:
                self.diagnostics()
            if self.base and self.media_inventory() != self.media_hashes:
                raise AssertionError("Fixture media changed")
        finally:
            if self.container_id:
                self.owned()
                self.docker("rm", "-f", self.container_id)
            self.temporary.cleanup()


def main():
    inventory()
    with Session() as session:
        session.restart()
        session.assert_loaded()
    print(
        "PASS: fresh official-image package installation and restart; fixture media unchanged"
    )


if __name__ == "__main__":
    main()
