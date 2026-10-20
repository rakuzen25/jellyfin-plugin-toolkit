"""Bounded, data-only synchronization of toolkit bindings in consumer checkouts."""

import argparse
import difflib
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jellyfin_plugin_toolkit import bootstrap
from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT
from jellyfin_plugin_toolkit.operations import replaces

INTEGRATIONS = {
    "eslint": "configs/eslint.mjs",
    "ruff": "configs/ruff.toml",
    "prettier": "configs/prettier.mjs",
    "typescript": "configs/tsconfig.json",
    "msbuild": "msbuild/Plugin.props",
    "frontend": "node/build.mjs",
}
DESCRIPTOR = {"schema": 1, "launcher": "bootstrap/plugin", "integrations": INTEGRATIONS}


def descriptor(files):
    data = files.get("toolkit-bindings.json", b"")
    if len(data) > 16_384:
        raise ValueError("Toolkit binding descriptor exceeds size limit")
    try:
        actual = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Missing or invalid toolkit binding descriptor") from error
    if actual != DESCRIPTOR or type(actual.get("schema")) is not int:
        raise ValueError("Unsupported toolkit binding contract")
    if any(name not in files for name in [actual["launcher"], *INTEGRATIONS.values()]):
        raise ValueError(
            "Toolkit binding descriptor references missing integration files"
        )
    return actual


def safe_path(root, name):
    path = root / name
    if not path.is_relative_to(root) or ".." in Path(name).parts:
        raise ValueError("Unsafe binding path: " + str(name))
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Binding cannot follow a symlink: " + str(name))
    return path


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def base_revision(root, ref):
    return (
        git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
        .decode()
        .strip()
    )


def workflow_references(text, repository):
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
        if re.search(r"\buses\s*:", line) and repository + "/" in line:
            if not pattern.fullmatch(line):
                raise ValueError(
                    "Ambiguous toolkit workflow/action reference: " + line.strip()
                )
    return matches


def reference_tag(match):
    return re.search(r"(?<!\S)v\d+\.\d+\.\d+(?:-[\w.]+)?(?!\S)", match["comment"] or "")


def rewrite_workflow(text, old, new, files):
    metadata = json.loads(files["toolkit-release.json"])
    revision = metadata.get("revision", "")
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Toolkit references require an immutable source revision")
    updates = []
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


def skip_space(text, index):
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def preset_span(text):
    """Locate the top-level extends value without reserializing unrelated JSON."""
    decoder = json.JSONDecoder()
    index = text.index("{") + 1
    found = None
    seen = set()
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
            if not isinstance(value, list) or any(
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


def rewrite_preset(text, old, new):
    if not isinstance(json.loads(text), dict):
        raise ValueError("Renovate configuration must be an object")
    span = preset_span(text)
    if span is None:
        return text
    start, end = span
    prefix = "github>" + new["repository"] + "#"

    def replace(match):
        value = json.loads(match[0])
        if not value.startswith(prefix):
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


def change_for(root, name, after, mode=None):
    path = safe_path(root, name)
    before = path.read_bytes() if path.exists() else None
    before_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    mode = mode if mode is not None else before_mode or 0o644
    if before == after and before_mode == mode:
        return None
    return Change(name, before, after, before_mode, mode)


def plan(root, base_ref=None, *, download=False, git_root=None):
    if transaction_path(root).exists():
        raise ValueError(
            "Interrupted synchronization; run ./plugin toolkit sync --recover"
        )
    new = json.loads(safe_path(root, "toolkit.lock.json").read_text())
    _, files = bootstrap.load_archive(root, new, download)
    descriptor(files)
    git_root = git_root or root
    if base_ref:
        revision = base_revision(git_root, base_ref)
        old = json.loads(git(git_root, "show", revision + ":toolkit.lock.json"))
    else:
        old = new
        # The active verified installation is the implicit base. Reading a new
        # lock must never cause synchronization to execute proposed-release code.
        if re.fullmatch(r"[a-f0-9]{64}", TOOLKIT_ROOT.name):
            record = safe_path(
                root, ".build/toolkit/" + TOOLKIT_ROOT.name + ".lock.json"
            )
            old = json.loads(record.read_text())
    if old["repository"] != new["repository"]:
        raise ValueError("Base and target toolkit repositories must match")
    _, previous = bootstrap.load_archive(root, old, download)
    descriptor(previous)
    old = {**old, "revision": json.loads(previous["toolkit-release.json"])["revision"]}
    launcher = safe_path(root, "plugin")
    current = launcher.read_bytes() if launcher.exists() else None
    canonical = files["bootstrap/plugin"]
    upgrading = old["sha256"] != new["sha256"]
    if base_ref:
        base_launcher = git(git_root, "show", revision + ":plugin")
        if base_launcher != previous["bootstrap/plugin"]:
            raise ValueError(
                "Base launcher has drift; repair the base before upgrading"
            )
    if base_ref or upgrading:
        if current not in {previous["bootstrap/plugin"], canonical}:
            raise ValueError("Local launcher edits; repair them before upgrading")
    changes = []
    # Git records executable permission, not the checkout's group-write policy.
    launcher_mode = (
        stat.S_IMODE(launcher.stat().st_mode) if launcher.exists() else 0o644
    )
    update = change_for(root, "plugin", canonical, (launcher_mode & 0o777) | 0o111)
    if update:
        changes.append(update)
    directory = safe_path(root, ".github/workflows")
    for path in sorted(directory.glob("*")):
        if path.suffix not in {".yml", ".yaml"}:
            continue
        name = path.relative_to(root).as_posix()
        text = safe_path(root, name).read_text(newline="")
        if not workflow_references(text, new["repository"]):
            continue
        if base_ref:
            try:
                baseline = git(git_root, "show", revision + ":" + name).decode()
            except subprocess.CalledProcessError:
                raise ValueError(
                    "New workflow requires explicit integration: " + name
                ) from None
            # Existing managed references must themselves have had a clean baseline.
            if rewrite_workflow(baseline, old, old, previous) != baseline:
                raise ValueError("Base workflow references have drift: " + name)
        rewritten = rewrite_workflow(text, old, new, files)
        update = change_for(root, name, rewritten.encode())
        if update:
            changes.append(update)
    preset = safe_path(root, "renovate.json")
    if preset.exists():
        text = preset.read_text(newline="")
        if base_ref:
            baseline = git(git_root, "show", revision + ":renovate.json").decode()
            if rewrite_preset(baseline, old, old) != baseline:
                raise ValueError("Base toolkit preset has drift")
        update = change_for(
            root, "renovate.json", rewrite_preset(text, old, new).encode()
        )
        if update:
            changes.append(update)
    return changes


def transaction_path(root):
    return safe_path(root, ".build/binding-transaction")


def write_durable(path, data, mode=0o644):
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def validate_original(root, item):
    path = safe_path(root, item.name)
    actual = path.read_bytes() if path.exists() else None
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    if actual != item.before or mode != item.before_mode:
        raise ValueError("Binding changed during synchronization: " + item.name)


def recover(root):
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
    originals = []
    for item in manifest:
        name = item["name"]
        if name not in {
            "plugin",
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


def apply(root, changes):
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
                "Interrupted synchronization; run ./plugin toolkit sync --recover"
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


def preview(changes):
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="./plugin toolkit sync", allow_abbrev=False)
    parser.add_argument(
        "--root", type=Path, default=Path(os.environ.get("JELLYFIN_PLUGIN_ROOT", "."))
    )
    parser.add_argument("--base-ref")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="explicitly restore an edited launcher at the same locked release",
    )
    parser.add_argument(
        "--download", action="store_true", help="fetch missing verified archives"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--recover", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.recover:
            build = safe_path(root, ".build")
            build.mkdir(exist_ok=True)
            with safe_path(root, ".build/.bindings.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                recover(root)
            print("Interrupted binding transaction recovered.")
            return 0
        if transaction_path(root).exists():
            raise ValueError(
                "Interrupted synchronization; run ./plugin toolkit sync --recover"
            )
        if args.repair and args.base_ref:
            raise ValueError("Repair the current release separately before upgrading")
        changes = plan(root, args.base_ref, download=args.download)
        preview(changes)
        if args.check and changes:
            return 1
        if args.apply:
            active_differs = (
                bool(re.fullmatch(r"[a-f0-9]{64}", TOOLKIT_ROOT.name))
                and TOOLKIT_ROOT.name
                != json.loads((root / "toolkit.lock.json").read_text())["sha256"]
            )
            if (
                not args.base_ref
                and not active_differs
                and not args.repair
                and any(
                    item.name == "plugin" and item.before != item.after
                    for item in changes
                )
            ):
                raise ValueError(
                    "Local launcher drift; review the preview, then run toolkit sync --apply --repair"
                )
            apply(root, changes)
        return 0
    except (
        ValueError,
        OSError,
        tarfile.TarError,
        subprocess.CalledProcessError,
    ) as error:
        parser.exit(1, f"Toolkit synchronization failed: {error}\n")


def check_integrations(root, config):
    """Check bindings for default components; consumer overrides retain ownership."""
    checks = config.validation.check_targets
    tooling = "tooling" in checks and not replaces(config, "check-tooling")
    frontend = config.frontend
    frontend_checks = "frontend" in checks and not replaces(config, "check-frontend")
    expected = []
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


if __name__ == "__main__":
    raise SystemExit(main())
