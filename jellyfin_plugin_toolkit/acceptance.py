"""Check actual distribution bytes independently of package assembly."""

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

from jellyfin_plugin_toolkit.build.backend import HOST_LIBRARIES
from jellyfin_plugin_toolkit.common.source_inventory import source_files
from jellyfin_plugin_toolkit.context import Context


def members(package):
    names = package.namelist()
    if len(names) != len(set(names)):
        raise ValueError("Duplicate distribution member")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("Unsafe distribution member")
    return set(names)


def inventory():
    ctx = Context.load()
    dist = ctx.root / "dist"
    binary = f"{ctx.prefix}-{ctx.version}.zip"
    source = f"{ctx.prefix}-source-{ctx.version}.zip"
    expected = {binary, source, "manifest.json"}
    if ctx.config["distribution"].get("web", False):
        expected.add(f"{ctx.prefix}-web-{ctx.version}.zip")
    sums = {}
    for line in (dist / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if name in sums or name not in expected:
            raise ValueError("Unexpected checksum member")
        sums[name] = digest
        if hashlib.sha256((dist / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Distribution checksum changed")
    if set(sums) != expected:
        raise ValueError("Missing distribution checksum")
    with zipfile.ZipFile(dist / binary) as package:
        names = members(package)
        metadata = json.loads(package.read("meta.json"))
        if (
            metadata["guid"] != ctx.identity["guid"]
            or metadata["name"] != ctx.identity["name"]
        ):
            raise ValueError("Package identity differs")
        inventory = json.loads(package.read("dependency-inventory.json"))["files"]
        binaries = {n for n in names if n.endswith(".dll")}
        if binaries != {e["file"] for e in inventory if e["file"].endswith(".dll")}:
            raise ValueError("Binary inventory is incomplete")
        if ctx.identity["assembly"] + ".dll" not in binaries:
            raise ValueError("Host assembly is missing")
        for name in binaries:
            if Path(name).name.startswith(HOST_LIBRARIES):
                raise ValueError("Host-owned binary was distributed")
        for entry in inventory:
            if (
                hashlib.sha256(package.read(entry["file"])).hexdigest()
                != entry["sha256"]
            ):
                raise ValueError("Inventory hash differs")
            if not entry["license"] or not set(entry["notices"]) <= names:
                raise ValueError("Required licensing material missing")
        info = json.loads(package.read("build-info.json"))
        lock = json.loads((ctx.root / "toolkit.lock.json").read_text())
        if info["toolkit"] != lock:
            raise ValueError("Package toolkit provenance differs")
    with zipfile.ZipFile(dist / source) as package:
        names = members(package)
        expected_source = {p.as_posix() for p in source_files(ctx.root)} | {
            "build-support/toolkit.tar.gz"
        }
        if names != expected_source:
            raise ValueError("Corresponding source inventory differs")
        for path in source_files(ctx.root):
            if package.read(path.as_posix()) != (ctx.root / path).read_bytes():
                raise ValueError("Corresponding source changed")
        if (
            hashlib.sha256(package.read("build-support/toolkit.tar.gz")).hexdigest()
            != lock["sha256"]
        ):
            raise ValueError("Corresponding toolkit source differs")
    manifest = json.loads((dist / "manifest.json").read_text())[0]
    if (
        manifest["guid"] != ctx.identity["guid"]
        or manifest["versions"][0]["checksum"]
        != hashlib.md5((dist / binary).read_bytes()).hexdigest()
    ):
        raise ValueError("Manifest differs from payload")
    print(
        "PASS: package inventory, licensing, source, toolkit and manifest match exact bytes"
    )
