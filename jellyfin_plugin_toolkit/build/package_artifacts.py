"""Assemble declared payloads, notices and corresponding source deterministically."""

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from jellyfin_plugin_toolkit.build.backend import HOST_LIBRARIES
from jellyfin_plugin_toolkit.common.release_metadata import load_release
from jellyfin_plugin_toolkit.common.source_inventory import source_files
from jellyfin_plugin_toolkit.context import Context


def archive(target, files):
    seen = set()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
        for source, name in sorted(files, key=lambda pair: str(pair[1])):
            name = str(name)
            if name in seen:
                raise ValueError("Duplicate distribution path: " + name)
            seen.add(name)
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (
                0o755 if source.stat().st_mode & 0o111 else 0o644
            ) << 16
            output.writestr(info, source.read_bytes())
    return target


def assemble(staging, release_mode=False):
    ctx = Context.load()
    root = ctx.root
    release = load_release(root)
    version = release.version
    prefix = ctx.prefix
    repository = os.environ.get(
        "JELLYFIN_PLUGIN_REPOSITORY_URL",
        "https://github.com/" + ctx.identity["repository"],
    )
    download = os.environ.get(
        "JELLYFIN_PLUGIN_DOWNLOAD_BASE_URL", "http://127.0.0.1:18098"
    )
    if release_mode:
        for value in (repository, download):
            parsed = urlparse(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "Release artifacts require public HTTPS repository and download URLs"
                )
    plugin = staging / "plugin"
    notices = plugin / "notices"
    notices.mkdir(exist_ok=True)
    for binary in plugin.glob("*.dll"):
        if binary.name.startswith(HOST_LIBRARIES):
            binary.unlink()
    distribution = ctx.config["distribution"]
    for entry in distribution.get("notices", []):
        source = ctx.path(entry["source"])
        destination = notices / entry.get("name", source.name)
        if not source.is_file():
            raise ValueError("Missing distribution notice: " + str(source))
        shutil.copy2(source, destination)
    for entry in distribution.get("payload_notices", []):
        source = plugin / entry["source"]
        if not source.is_file():
            raise ValueError("Missing payload notice: " + str(source))
        shutil.copy2(source, notices / entry["name"])
    license_entries = distribution.get("licenses", {})
    inventory = []
    dependencies = {}
    lock = root / ctx.config["backend"]["entry"]
    lock = lock.parent / "packages.lock.json"
    if lock.exists():
        for framework in json.loads(lock.read_text())["dependencies"].values():
            dependencies.update(framework)
    for path in sorted(plugin.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(plugin).as_posix()
        entry = license_entries.get(path.name)
        if path.suffix == ".dll" and entry is None:
            raise ValueError(
                "No declared license inventory for shipped binary: " + relative
            )
        if entry is None:
            entry = next(
                (
                    item
                    for item in distribution.get("licensed_paths", [])
                    if relative.startswith(item["prefix"])
                ),
                None,
            )
        if entry is None:
            continue
        required = entry["notices"]
        for notice in required:
            if not (notices / notice).is_file():
                raise ValueError("Missing required notice: " + notice)
        package = entry.get("package", "project-source")
        inventory.append(
            {
                "file": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "package": package,
                "version": entry.get(
                    "version", dependencies.get(package, {}).get("resolved", version)
                ),
                "license": entry.get("license", "Apache-2.0"),
                "notices": ["notices/" + name for name in required],
            }
        )
    lock_data = json.loads((root / "toolkit.lock.json").read_text())
    (plugin / "dependency-inventory.json").write_text(
        json.dumps({"files": inventory}, indent=2) + "\n"
    )
    from jellyfin_plugin_toolkit.build.backend import reference_directory
    from jellyfin_plugin_toolkit.dev.references import inventory as reference_inventory

    host = reference_inventory(reference_directory())
    (plugin / "build-info.json").write_text(
        json.dumps(
            {
                "version": version,
                "targetAbi": release.abi,
                "hostProvenance": host,
                "repositoryUrl": repository,
                "editableSourceArchive": f"{prefix}-source-{version}.zip",
                "toolkit": lock_data,
                "binaryDistributionLicense": "GPL-3.0-only",
            },
            indent=2,
        )
        + "\n"
    )
    (plugin / "meta.json").write_text(
        json.dumps(
            {
                "guid": release.guid,
                "name": release.name,
                "description": release.description,
                "version": release.jellyfin_version,
                "targetAbi": release.abi,
                "status": "Active",
                "autoUpdate": False,
                "assemblies": [p.name for p in sorted(plugin.glob("*.dll"))],
            },
            indent=2,
        )
        + "\n"
    )
    out = root / "dist"
    out.mkdir(exist_ok=True)
    binary = archive(
        out / f"{prefix}-{version}.zip",
        [(p, p.relative_to(plugin)) for p in plugin.rglob("*") if p.is_file()],
    )
    products = [binary]
    if distribution.get("web", False):
        excluded = set(distribution.get("web_exclude", ["demo.html", "demo.wav"]))
        files = [
            (p, p.relative_to(root / "dist/web"))
            for p in (root / "dist/web").rglob("*")
            if p.is_file() and p.name not in excluded
        ]
        existing = {str(n) for _, n in files}
        files.extend(
            (ctx.path(e["source"]), Path(e["name"]))
            for e in distribution.get("web_notices", [])
            if e["name"] not in existing
        )
        products.append(archive(out / f"{prefix}-web-{version}.zip", files))
    toolkit_archive = root / ".build/toolkit" / (lock_data["sha256"] + ".tar.gz")
    if (
        not toolkit_archive.is_file()
        or hashlib.sha256(toolkit_archive.read_bytes()).hexdigest()
        != lock_data["sha256"]
    ):
        raise ValueError("Matching toolkit source archive is missing")
    sources = [
        (root / p, p)
        for p in source_files(root)
        if str(p) != "build-support/toolkit.tar.gz"
    ]
    sources.append((toolkit_archive, Path("build-support/toolkit.tar.gz")))
    products.append(archive(out / f"{prefix}-source-{version}.zip", sources))
    manifest = [
        {
            "guid": release.guid,
            "name": release.name,
            "owner": release.owner,
            "overview": release.overview,
            "description": release.description,
            "category": release.category,
            "versions": [
                {
                    "version": release.jellyfin_version,
                    "changelog": release.changelog,
                    "targetAbi": release.abi,
                    "sourceUrl": download.rstrip("/") + "/" + binary.name,
                    "checksum": hashlib.md5(binary.read_bytes()).hexdigest(),
                    "timestamp": release.timestamp,
                }
            ],
        }
    ]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    products.append(out / "manifest.json")
    (out / "SHA256SUMS").write_text(
        "".join(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
            for p in products
        )
    )
    return products
