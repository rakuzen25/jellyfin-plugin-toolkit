"""Source, toolkit and exact artifact fingerprints."""

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from jellyfin_plugin_toolkit.common.source_inventory import (
    frontend_inputs,
    source_files,
)
from jellyfin_plugin_toolkit.context import Context


def digest(root: Path, paths: Iterable[Path], normalize_version: bool = False) -> str:
    value = hashlib.sha256()
    for path in sorted(paths):
        data = (root / path).read_bytes()
        if normalize_version and str(path) == "version.txt":
            data = b"RELEASE\n"
        if normalize_version and path.name == "packages.lock.json":
            context = Context.load(root)
            own = {
                name.casefold()
                for name in [
                    context.identity.assembly,
                    *context.config.backend.project_assemblies,
                ]
            }
            lock = json.loads(data)
            for framework in lock["dependencies"].values():
                for dependency in framework.values():
                    if dependency.get("type") == "Project":
                        for name in dependency.get("dependencies", {}):
                            if name.casefold() in own:
                                dependency["dependencies"][name] = "[RELEASE, )"
            data = json.dumps(lock, sort_keys=True).encode()
        value.update(str(path).encode() + b"\0" + data + b"\0")
    return value.hexdigest()


def artifact_hashes(root: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((root / "dist").iterdir())
        if p.suffix == ".zip" or p.name in {"manifest.json", "SHA256SUMS"}
    }


def web_inputs(root: Path) -> dict[str, Any]:
    ctx = Context.load(root)
    return {
        "input": digest(root, frontend_inputs(ctx, source_files(root))),
        "files": {
            p.relative_to(root / "dist/web").as_posix(): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in sorted((root / "dist/web").rglob("*"))
            if p.is_file()
        },
    }


def verify_web(root: Path) -> None:
    if json.loads((root / "dist/web-build.json").read_text()) != web_inputs(root):
        raise ValueError("Prepared frontend inputs or asset hashes changed")
