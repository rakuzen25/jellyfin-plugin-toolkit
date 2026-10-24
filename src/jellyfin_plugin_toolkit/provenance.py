"""Read uv's dependency records; retain exact build tools with corresponding source."""

import hashlib
import json
import re
import tomllib
import urllib.request
from importlib.metadata import version
from pathlib import Path
from typing import Any

REPOSITORY = "rakuzen25/jellyfin-plugin-toolkit"
NAME = "jellyfin-plugin-toolkit"
TOOLKIT_DIST = Path(".build/toolkit-dist")


def package_version() -> str:
    return version(NAME)


def wheel_name(selected: str | None = None) -> str:
    return f"jellyfin_plugin_toolkit-{selected or package_version()}-py3-none-any.whl"


def release_url(selected: str | None = None, filename: str | None = None) -> str:
    selected = selected or package_version()
    return f"https://github.com/{REPOSITORY}/releases/download/v{selected}/{filename or wheel_name(selected)}"


def public_revision(selected: str) -> str:
    """Resolve a public published dependency; draft-release inspection stays authenticated."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", selected):
        raise ValueError("Expected a stable toolkit version")

    def get(endpoint: str) -> Any:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}{endpoint}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "jpt"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    published = get("/releases/tags/v" + selected)
    if published.get("draft") is not False:
        raise ValueError("Starter generation requires a published toolkit release")
    reference = get("/git/ref/tags/v" + selected)["object"]
    for _ in range(4):
        if reference.get("type") == "commit" and re.fullmatch(
            r"[0-9a-f]{40}", reference.get("sha", "")
        ):
            return str(reference["sha"])
        if reference.get("type") != "tag":
            break
        reference = get("/git/tags/" + reference["sha"])["object"]
    raise ValueError("Toolkit release tag does not resolve to a commit")


def record(root: Path, *, release: bool = False) -> dict[str, Any]:
    root = Path(root)
    payload = (root / "uv.lock").read_bytes()
    lock = tomllib.loads(payload.decode())
    entries = [
        p for p in lock.get("package", []) if p["name"].replace("_", "-") == NAME
    ]
    if len(entries) != 1:
        raise ValueError("uv.lock must contain exactly one toolkit dependency")
    entry = entries[0]
    if not isinstance(entry["version"], str) or not re.fullmatch(
        r"\d+\.\d+\.\d+", entry["version"]
    ):
        raise ValueError("Toolkit provenance requires a stable release version")
    source = entry["source"]
    wheels = entry.get("wheels", [])
    url = source.get("url") or source.get("path")
    if not url or not str(url).endswith(".whl"):
        raise ValueError(
            "Builds require a locked toolkit wheel; use a candidate wheel for integration tests"
        )
    hashes = [w["hash"].removeprefix("sha256:") for w in wheels if "hash" in w]
    if len(hashes) != 1 or not re.fullmatch("[0-9a-f]{64}", hashes[0]):
        raise ValueError("Toolkit wheel must have one locked SHA256")
    digest = hashes[0]
    if source.get("path"):
        if source["path"] != (TOOLKIT_DIST / wheel_name(entry["version"])).as_posix():
            raise ValueError(
                "Place candidate wheels under .build/toolkit-dist with their native filename"
            )
        distribution_directory(root)
        path = root / source["path"]
        if path.is_symlink():
            raise ValueError("Candidate wheels cannot follow symlinks")
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("Candidate wheels must be inside the consumer checkout")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Candidate wheel differs from its locked SHA256")
    if source.get("url") and url != release_url(entry["version"]):
        raise ValueError(
            "Toolkit dependencies require a canonical GitHub release wheel"
        )
    if release and url != release_url(entry["version"]):
        raise ValueError(
            "Published consumers require a canonical released GitHub wheel"
        )
    return {
        "distribution": NAME,
        "version": entry["version"],
        "source": url,
        "sha256": digest,
        "python_lock_sha256": hashlib.sha256(payload).hexdigest(),
    }


def identity(root: Path) -> dict[str, str]:
    """The validator package changes policy; unrelated Python dev dependencies do not."""
    return {k: v for k, v in record(root).items() if k != "python_lock_sha256"}


def distribution_directory(root: Path) -> Path:
    """Validate retained distribution parents before reading or writing artifacts."""
    root = Path(root).resolve()
    directory = root / TOOLKIT_DIST
    for path in (directory.parent, directory):
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise ValueError(
                "Toolkit distribution directories cannot follow symlinks or special files"
            )
    return directory


def distributions(root: Path) -> list[Path]:
    root = Path(root).resolve()
    selected = record(root)
    directory = distribution_directory(root)
    wheel = directory / wheel_name(selected["version"])
    sdist = directory / f"jellyfin_plugin_toolkit-{selected['version']}.tar.gz"
    checksums = directory / f"toolkit-{selected['version']}-SHA256SUMS"
    if directory.is_symlink() or any(
        path.is_symlink() or path.exists() and not path.is_file()
        for path in [wheel, sdist, checksums]
    ):
        raise ValueError(
            "Corresponding toolkit distributions cannot follow symlinks or special files"
        )
    directory.mkdir(parents=True, exist_ok=True)
    if not wheel.exists():
        with urllib.request.urlopen(selected["source"], timeout=60) as response:
            wheel.write_bytes(response.read())
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != selected["sha256"]:
        raise ValueError("Corresponding toolkit wheel differs from uv.lock")
    if not sdist.exists():
        with urllib.request.urlopen(
            release_url(selected["version"], sdist.name), timeout=60
        ) as response:
            sdist.write_bytes(response.read())
    if not selected["source"].startswith(TOOLKIT_DIST.as_posix() + "/") and selected[
        "source"
    ].startswith("https://"):
        if not checksums.exists():
            with urllib.request.urlopen(
                release_url(selected["version"], "SHA256SUMS"), timeout=60
            ) as response:
                checksums.write_bytes(response.read())
        expected = {}
        for line in checksums.read_text().splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
            if not match or match[2] in expected:
                raise ValueError("Invalid toolkit distribution checksum inventory")
            expected[match[2]] = match[1]
        actual = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [wheel, sdist]
        }
        if expected != actual:
            raise ValueError(
                "Toolkit wheel/source distribution pair differs from release checksums"
            )
        return [wheel, sdist, checksums]
    # Candidate pairs come from the same native build and remain in verified source inventory.
    return [wheel, sdist]
