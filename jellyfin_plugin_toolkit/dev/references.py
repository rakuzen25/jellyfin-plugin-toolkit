"""Ensure a verified cache extracted from the pinned official Jellyfin image."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from jellyfin_plugin_toolkit.common.process import IMAGES, ROOT, run
from jellyfin_plugin_toolkit.common.release_metadata import load_identity


def inventory(directory: Path) -> dict:
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.iterdir())
        if p.suffix in {".dll", ".so"}
    }
    if (
        not {
            "MediaBrowser.Common.dll",
            "MediaBrowser.Controller.dll",
            "MediaBrowser.Model.dll",
        }
        <= files.keys()
    ):
        raise ValueError("Incomplete official references")
    if (directory / "image.txt").read_text().strip() != IMAGES["jellyfin"]:
        raise ValueError("Reference image changed")
    return {
        "image": IMAGES["jellyfin"],
        "platform": IMAGES["platform"],
        "abi": load_identity().abi,
        "files": files,
    }


def verify(directory: Path) -> bool:
    try:
        return json.loads(
            (directory / "cache-integrity.json").read_text()
        ) == inventory(directory)
    except OSError, ValueError:
        return False


def ensure(directory: Path = ROOT / ".build/jellyfin") -> Path:
    if verify(directory):
        return directory
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="references-", dir=directory.parent
    ) as temporary:
        staged = Path(temporary) / "assemblies"
        staged.mkdir()
        container = subprocess.check_output(
            ["docker", "create", "--platform", IMAGES["platform"], IMAGES["jellyfin"]],
            text=True,
        ).strip()
        try:
            run("docker", "cp", container + ":/jellyfin/.", str(staged))
        finally:
            run("docker", "rm", container)
        deps = json.loads((staged / "jellyfin.deps.json").read_text())
        abi = load_identity().abi
        expected = ".".join(str(abi).split(".")[:3])
        if not all(
            key + "/" + expected in deps["libraries"]
            for key in ("Jellyfin.Common", "Jellyfin.Controller", "Jellyfin.Model")
        ):
            raise ValueError(
                "Official image does not match the configured Jellyfin ABI"
            )
        runtime = json.loads((staged / "jellyfin.runtimeconfig.json").read_text())
        if runtime["runtimeOptions"]["tfm"] != "net10.0":
            raise ValueError("Expected .NET 10 Jellyfin runtime")
        cache = Path(temporary) / "cache"
        cache.mkdir()
        for file in staged.iterdir():
            if file.suffix in {".dll", ".so"}:
                shutil.copy2(file, cache / file.name)
        (cache / "image.txt").write_text(IMAGES["jellyfin"] + "\n")
        (cache / "cache-integrity.json").write_text(
            json.dumps(inventory(cache), indent=2) + "\n"
        )
        if directory.exists():
            shutil.rmtree(directory)
        shutil.move(str(cache), directory)
    return directory


if __name__ == "__main__":
    ensure()
