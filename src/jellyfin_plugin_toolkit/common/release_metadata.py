"""Plugin identity and release metadata from the consumer's authoritative inputs."""

import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from jellyfin_plugin_toolkit.context import Context


@dataclass(frozen=True)
class PluginIdentity:
    version: str
    abi: str
    guid: str
    name: str
    owner: str
    overview: str
    description: str
    category: str

    @property
    def jellyfin_version(self) -> str:
        return self.version + ".0"


@dataclass(frozen=True)
class Release(PluginIdentity):
    changelog: str
    timestamp: str


def load_identity(root: Path | None = None) -> PluginIdentity:
    context = Context.load(root)
    return PluginIdentity(
        version=context.version,
        abi=context.config.host.abi,
        **{
            key: getattr(context.identity, key)
            for key in ("guid", "name", "owner", "overview", "description", "category")
        },
    )


def changelog_section(root: Path, version: str) -> tuple[str, str]:
    text = (root / "CHANGELOG.md").read_text()
    heading = re.search(
        r"^##\s+\[?" + re.escape(version) + r"(?:\]|(?=\s|$)).*$", text, re.MULTILINE
    )
    if heading is None:
        raise ValueError("Missing changelog section for " + version)
    stamp = re.search(r"\((\d{4}-\d{2}-\d{2})\)\s*$", heading.group())
    if stamp is None:
        raise ValueError("Missing release date in changelog heading")
    tail = text[heading.end() :]
    next_heading = re.search(r"^##\s", tail, re.MULTILINE)
    notes = tail[: next_heading.start()] if next_heading else tail
    if not notes.strip():
        raise ValueError("Empty release notes")
    return notes.strip(), date.fromisoformat(stamp[1]).isoformat() + "T00:00:00Z"


def load_release(root: Path | None = None) -> Release:
    context = Context.load(root)
    identity = load_identity(context.root)
    notes, timestamp = changelog_section(context.root, identity.version)
    return Release(**asdict(identity), changelog=notes, timestamp=timestamp)


def changelog_for_version(root: Path, version: str) -> str:
    return changelog_section(root, version)[0]
