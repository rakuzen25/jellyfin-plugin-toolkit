"""Bounded, data-only synchronization of toolkit bindings in consumer checkouts."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from jellyfin_plugin_toolkit.common.json_shapes import is_json_array, is_json_object

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.configuration import Configuration

import difflib
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from jellyfin_plugin_toolkit.operations import replaces
from jellyfin_plugin_toolkit.provenance import REPOSITORY, record, wheel_name


def safe_path(root: Path, name: str | Path) -> Path:
    path = root / name
    if not path.is_relative_to(root) or ".." in Path(name).parts:
        raise ValueError("Unsafe binding path: " + str(name))
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Binding cannot follow a symlink: " + str(name))
    return path


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def base_revision(root: Path, ref: str) -> str:
    return (
        git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
        .decode()
        .strip()
    )


def workflow_references(text: str, repository: str) -> list[re.Match[str]]:
    pattern = re.compile(
        r"^(?P<prefix>[ \t]*(?:-[ \t]+)?uses:[ \t]*)(?P<quote>['\"]?)"
        + re.escape(repository)
        + r"/(?P<component>\.github/(?:workflows/[\w.-]+\.ya?ml|actions/[\w./-]+))"
        + r"@(?P<revision>[^\s#'\"]+)(?P=quote)[ \t]*(?:#(?P<comment>[^\r\n]*))?\r?$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    # Refuse unsupported spellings rather than rewriting an ambiguous YAML document.
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if (
            re.search(r"\buses\s*:", line)
            and repository + "/" in line
            and not pattern.fullmatch(line)
        ):
            raise ValueError(
                "Ambiguous toolkit workflow/action reference: " + line.strip()
            )
    return matches


def reference_tag(match: re.Match[str]) -> re.Match[str] | None:
    return re.search(r"(?<!\S)v\d+\.\d+\.\d+(?:-[\w.]+)?(?!\S)", match["comment"] or "")


def rewrite_workflow(
    text: str, old: dict[str, str], new: dict[str, str], files: dict[str, bytes]
) -> str:
    metadata = json.loads(files["toolkit-release.json"])
    revision = metadata.get("revision", "")
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Toolkit references require an immutable source revision")
    updates: list[tuple[int, int, str]] = []
    for match in workflow_references(text, new["repository"]):
        component = match["component"]
        if component not in files and not any(
            name.startswith(component + "/") for name in files
        ):
            raise ValueError("Referenced toolkit component was removed: " + component)
        tag = reference_tag(match)
        if match["revision"] not in {old["revision"], revision} or (
            tag and tag[0] not in {old["version"], new["version"]}
        ):
            raise ValueError(
                "Toolkit reference drift; repair the reference before upgrading"
            )
        updates.append((match.start("revision"), match.end("revision"), revision))
        if tag:
            offset = match.start("comment") + tag.start()
            updates.append((offset, offset + len(tag[0]), new["version"]))
        else:
            raise ValueError("Toolkit reference is missing its release-version comment")
    for start, end, value in sorted(updates, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def preset_span(text: str) -> tuple[int, int] | None:
    """Locate the top-level extends value without reserializing unrelated JSON."""
    decoder = json.JSONDecoder()
    index = text.index("{") + 1
    found = None
    seen: set[str] = set()
    while True:
        index = skip_space(text, index)
        if text[index] == "}":
            break
        key, index = decoder.raw_decode(text, index)
        if key in seen:
            raise ValueError("Duplicate JSON configuration key: " + key)
        seen.add(key)
        index = skip_space(text, index)
        if text[index] != ":":
            raise ValueError("Invalid Renovate JSON")
        index = skip_space(text, index + 1)
        start = index
        value, index = decoder.raw_decode(text, index)
        if key == "extends":
            if not is_json_array(value) or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError("Renovate extends must contain preset strings")
            found = (start, index)
        index = skip_space(text, index)
        if text[index] == ",":
            index += 1
        elif text[index] != "}":
            raise ValueError("Invalid Renovate JSON")
    return found


def rewrite_preset(text: str, old: dict[str, str], new: dict[str, str]) -> str:
    if not is_json_object(json.loads(text)):
        raise ValueError("Renovate configuration must be an object")
    span = preset_span(text)
    if span is None:
        return text
    start, end = span
    prefixes = (
        "github>" + new["repository"] + "#",
        "github>" + new["repository"] + ":tooling#",
    )

    def replace(match: re.Match[str]) -> str:
        value = json.loads(match[0])
        prefix = next((item for item in prefixes if value.startswith(item)), None)
        if prefix is None:
            return match[0]
        if value[len(prefix) :] not in {old["version"], new["version"]}:
            raise ValueError("Toolkit preset drift; repair it before upgrading")
        return json.dumps(prefix + new["version"])

    return (
        text[:start]
        + re.sub(r'"(?:[^"\\]|\\.)*"', replace, text[start:end])
        + text[end:]
    )


@dataclass(frozen=True)
class Change:
    name: str
    before: bytes | None
    after: bytes
    before_mode: int | None
    mode: int


def change_for(
    root: Path, name: str, after: bytes, mode: int | None = None
) -> Change | None:
    path = safe_path(root, name)
    before = path.read_bytes() if path.exists() else None
    before_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    mode = mode if mode is not None else before_mode or 0o644
    if before == after and before_mode == mode:
        return None
    return Change(name, before, after, before_mode, mode)


def target(root: Path) -> tuple[str, str]:
    data = tomllib.loads(safe_path(root, "pyproject.toml").read_text())
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    source = sources.get("jellyfin-plugin-toolkit", {})
    if source and set(source) != {"url"}:
        raise ValueError("Toolkit updates require only a canonical wheel URL source")
    url = source.get("url")
    if url is None:
        for dependency in data.get("project", {}).get("dependencies", []):
            if dependency.startswith("jellyfin-plugin-toolkit @ "):
                url = dependency.split(" @ ", 1)[1]
    pattern = (
        r"https://github.com/"
        + REPOSITORY
        + r"/releases/download/v(\d+\.\d+\.\d+)/jellyfin_plugin_toolkit-\1-py3-none-any\.whl"
    )
    match = re.fullmatch(pattern, url or "")
    if not match:
        raise ValueError("Toolkit updates require a canonical GitHub release wheel URL")
    return str(url), match[1]


def plan(
    root: Path,
    base_ref: str | None = None,
    *,
    download: bool = False,
    git_root: Path | None = None,
) -> list[Change]:
    from jellyfin_plugin_toolkit.release.service import (
        github_json,
        release_for_tag,
        tag_commit,
    )

    root = Path(root)
    from jellyfin_plugin_toolkit.resources_api import RESOURCE_ROOT

    if root.resolve() == RESOURCE_ROOT.parents[2]:
        from jellyfin_plugin_toolkit.typing_policy import materialize

        project = safe_path(root, "pyproject.toml")
        change = change_for(
            root, "pyproject.toml", materialize(project.read_text()).encode()
        )
        return [change] if change else []
    url, selected = target(root)
    tag = "v" + selected
    release = release_for_tag(REPOSITORY, tag)
    if not release or release.get("draft") is not False:
        raise ValueError("Toolkit binding updates require a published release")
    assets = [asset["name"] for asset in release.get("assets", [])]
    required = {wheel_name(selected), f"jellyfin_plugin_toolkit-{selected}.tar.gz"}
    if len(assets) != len(set(assets)) or not required.issubset(assets):
        raise ValueError(
            "Toolkit release must contain an unambiguous matching wheel and source distribution"
        )
    revision = tag_commit(REPOSITORY, tag)
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Toolkit release has no immutable source revision")
    with tempfile.TemporaryDirectory(prefix="jpt-update-") as folder:
        staged = Path(folder)
        for name in ("pyproject.toml", "uv.lock", ".python-version"):
            source = safe_path(root, name)
            if source.exists():
                shutil.copy2(source, staged / name)
        metadata = tomllib.loads((staged / "pyproject.toml").read_text())
        development = any(
            re.match(r"jellyfin[-_]plugin[-_]toolkit(?:\s|[<>=!~;\[]|$)", dependency)
            for dependency in metadata.get("dependency-groups", {}).get("dev", [])
            if isinstance(dependency, str)
        )
        subprocess.run(
            [
                "uv",
                "add",
                "--no-sync",
                "--no-build",
                *(["--dev"] if development else []),
                "jellyfin-plugin-toolkit @ " + url,
            ],
            cwd=staged,
            check=True,
        )
        selected_record = record(staged, release=True)
        project = staged / "pyproject.toml"
        if "mypy" in tomllib.loads(project.read_text()).get("tool", {}):
            from jellyfin_plugin_toolkit.typing_policy import materialize

            policy = released_policy(
                selected_record["source"], selected_record["sha256"]
            )
            project.write_text(materialize(project.read_text(), policy))
        new = {"repository": REPOSITORY, "version": tag, "revision": revision}
        if base_ref:
            old_lock = tomllib.loads(
                git(
                    git_root or root,
                    "show",
                    base_revision(git_root or root, base_ref) + ":uv.lock",
                ).decode()
            )
            old_version = next(
                p["version"]
                for p in old_lock["package"]
                if p["name"] == "jellyfin-plugin-toolkit"
            )
        else:
            old_version = record(root)["version"]
        old = {
            "repository": REPOSITORY,
            "version": "v" + old_version,
            "revision": tag_commit(REPOSITORY, "v" + old_version),
        }
        files = {
            "toolkit-release.json": json.dumps(
                {"version": selected_record["version"], "revision": revision}
            ).encode()
        }
        for entry in github_json(
            REPOSITORY, "/contents/.github/workflows?ref=" + revision
        ):
            if entry.get("type") == "file":
                files[entry["path"]] = b"workflow"
        changes: list[Change | None] = []
        for name in ("pyproject.toml", "uv.lock"):
            changes.append(change_for(root, name, (staged / name).read_bytes()))
        for workflow in sorted((root / ".github/workflows").glob("*.y*ml")):
            changes.append(
                change_for(
                    root,
                    workflow.relative_to(root).as_posix(),
                    rewrite_workflow(workflow.read_text(), old, new, files).encode(),
                )
            )
        preset = root / "renovate.json"
        if preset.exists():
            changes.append(
                change_for(
                    root,
                    "renovate.json",
                    rewrite_preset(preset.read_text(), old, new).encode(),
                )
            )
        return [change for change in changes if change]


def released_policy(url: str, digest: str) -> bytes:
    """Read a known data member from the resolved wheel, never import proposed code."""
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Released policy wheel differs from its locked SHA256")
    member = "jellyfin_plugin_toolkit/resources/configs/mypy.toml"
    with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
        matches = [entry for entry in wheel.infolist() if entry.filename == member]
        if len(matches) != 1 or matches[0].file_size > 65536:
            raise ValueError(
                "Released wheel has no unambiguous supported typing policy"
            )
        return wheel.read(matches[0])


def transaction_path(root: Path) -> Path:
    return safe_path(root, ".build/binding-transaction")


def write_durable(path: Path, data: bytes, mode: int = 0o644) -> None:
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def validate_original(root: Path, item: Change) -> None:
    path = safe_path(root, item.name)
    actual = path.read_bytes() if path.exists() else None
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    if actual != item.before or mode != item.before_mode:
        raise ValueError("Binding changed during synchronization: " + item.name)


def recover(root: Path) -> None:
    """Explicitly finish rollback after a process interruption; never discard new edits."""
    folder = transaction_path(root)
    if not folder.exists():
        return
    if (folder / "committed").exists():
        shutil.rmtree(folder)
        return
    manifest = json.loads(
        safe_path(root, ".build/binding-transaction/manifest.json").read_text()
    )
    originals: list[tuple[Path, bytes | None, int]] = []
    for item in manifest:
        name = item["name"]
        if name not in {
            "pyproject.toml",
            "uv.lock",
            "renovate.json",
            ".vscode/settings.json",
        } and not re.fullmatch(r"\.github/workflows/[\w.-]+\.ya?ml", name):
            raise ValueError("Invalid recovery destination")
        path = safe_path(root, name)
        before = bytes.fromhex(item["before"]) if item["before"] is not None else None
        actual = path.read_bytes() if path.exists() else None
        if actual not in {before, bytes.fromhex(item["after"])}:
            raise ValueError("New edit prevents recovery: " + name)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        if mode not in {item["before_mode"], item["mode"]}:
            raise ValueError("New permission edit prevents recovery: " + name)
        originals.append((path, before, item["before_mode"]))
    for index, (path, before, mode) in enumerate(originals):
        if before is None:
            path.unlink(missing_ok=True)
        else:
            restore = folder / ("restore-" + str(index))
            write_durable(restore, before, mode)
            os.replace(restore, path)
    shutil.rmtree(folder)


def apply(root: Path, changes: Sequence[Change]) -> None:
    """Stage all bytes and journal originals before replacement; recover interrupted writes."""
    if not changes:
        return
    build = safe_path(root, ".build")
    build.mkdir(exist_ok=True)
    with safe_path(root, ".build/.bindings.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        folder = transaction_path(root)
        if folder.exists():
            raise ValueError(
                "Interrupted synchronization; run uv run jpt toolkit recover"
            )
        for item in changes:
            validate_original(root, item)
        # Until the manifest is durable, no consumer file can have been changed.
        with tempfile.TemporaryDirectory(
            prefix="bindings-stage-", dir=build
        ) as temporary:
            staging = Path(temporary)
            for index, item in enumerate(changes):
                write_durable(staging / str(index), item.after, item.mode)
            manifest = [
                {
                    "name": item.name,
                    "before": item.before.hex() if item.before is not None else None,
                    "after": item.after.hex(),
                    "before_mode": item.before_mode,
                    "mode": item.mode,
                }
                for item in changes
            ]
            write_durable(staging / "manifest.json", json.dumps(manifest).encode())
            staging.rename(folder)
            replacing = False
            try:
                for item in changes:
                    validate_original(root, item)
                replacing = True
                for index, item in enumerate(changes):
                    os.replace(folder / str(index), safe_path(root, item.name))
                write_durable(folder / "committed", b"complete")
            except BaseException:
                if replacing:
                    recover(root)
                else:
                    shutil.rmtree(folder)
                raise
            finally:
                if (folder / "committed").exists():
                    shutil.rmtree(folder)


def preview(changes: Sequence[Change]) -> None:
    for item in changes:
        print(
            "".join(
                difflib.unified_diff(
                    (item.before or b"").decode().splitlines(keepends=True),
                    item.after.decode().splitlines(keepends=True),
                    fromfile=item.name,
                    tofile=item.name,
                )
            ),
            end="",
        )
        if item.before_mode != item.mode:
            print(f"{item.name}: mode {item.before_mode!r} -> {oct(item.mode)}")
    if not changes:
        print("Toolkit bindings are synchronized.")


def check_integrations(root: Path, config: Configuration) -> None:
    """Check bindings for default components; consumer overrides retain ownership."""
    checks = config.validation.check_targets
    tooling = "tooling" in checks and not replaces(config, "check-tooling")
    frontend = config.frontend
    frontend_checks = "frontend" in checks and not replaces(config, "check-frontend")
    expected: list[tuple[str, str]] = []
    if tooling:
        expected.extend(
            [
                ("eslint.config.mjs", "configs/eslint.mjs"),
                ("pyproject.toml", "configs/ruff.toml"),
            ]
        )
    if frontend_checks:
        expected.extend(
            [
                ("eslint.config.mjs", "configs/eslint.mjs"),
                (
                    frontend.directory + "/tsconfig.json",
                    "configs/tsconfig.json",
                ),
            ]
        )
    if any(
        not replaces(config, "format-check-" + target)
        for target in checks
        if target != "backend"
    ):
        names = [
            name
            for name in [".prettierrc.mjs", "prettier.config.mjs"]
            if (root / name).is_file()
        ]
        if not names:
            raise ValueError(
                "Shared Prettier integration is missing; import configs/prettier.mjs or configure a formatting override"
            )
        expected.append((names[0], "configs/prettier.mjs"))
    expected.append(("Directory.Build.props", "msbuild/Plugin.props"))
    for name, integration in set(expected):
        path = safe_path(root, name)
        if not path.is_file() or integration not in path.read_text():
            raise ValueError(
                "Toolkit integration is missing: "
                + name
                + " must reference "
                + integration
            )
