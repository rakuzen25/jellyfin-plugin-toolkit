"""Independent synthetic consumer context for generic tooling tests."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jellyfin_plugin_toolkit.configuration import Configuration
from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.provenance import package_version
from jellyfin_plugin_toolkit.scaffold import toml

defaults = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "src/jellyfin_plugin_toolkit/resources/templates/defaults.json"
    ).read_text()
)
groups = [
    "metadata",
    "frontend",
    "backend",
    "tooling",
    "workflow",
    "rendering",
    "performance",
    "artifacts",
]
config: dict[str, Any] = {
    "schema": 1,
    "plugin": {
        "id": "example",
        "name": "Example",
        "guid": "e8f50bc1-2282-42c7-a64a-648987ad5dd9",
        "assembly": "Jellyfin.Plugin.Example",
        "repository": "example/plugin",
        "owner": "example",
        "description": "Example",
        "overview": "Example",
        "category": "General",
    },
    "host": defaults["host"],
    "images": defaults["images"],
    "backend": {
        "entry": "src/Host/Plugin.csproj",
        "projects": {"Host": "src/Host/Plugin.csproj"},
    },
    "frontend": {"enabled": True, "admin_entry": "admin/config.ts"},
    "validation": {
        "groups": groups,
        "rules": [{"patterns": ["**"], "groups": groups}],
        "job_names": {
            "rendering": [
                "rendering (chromium)",
                "rendering (mobile-chromium)",
                "rendering (firefox)",
                "rendering (webkit)",
            ],
            "performance": [
                "performance (stress-chromium)",
                "performance (stress-mobile-chromium)",
            ],
        },
    },
    "distribution": {"web": False},
}
config["validation"]["rules"] = [
    {"patterns": ["*.md", "docs/**"], "groups": []},
    {
        "patterns": [
            "plugin.toml",
            "uv.lock",
            "version.txt",
            ".github/**",
            ".editorconfig",
        ],
        "groups": groups,
    },
    {"patterns": ["src/**"], "groups": ["backend", "workflow", "artifacts"]},
    {
        "patterns": ["frontend/**"],
        "groups": ["frontend", "workflow", "rendering", "performance", "artifacts"],
    },
    {"patterns": ["renovate.json"], "groups": ["metadata"]},
]


def create_consumer(
    root: Path, selected: Configuration | dict[str, Any] | None = None
) -> Context:
    selected = copy.deepcopy(config if selected is None else selected)
    (root / "plugin.toml").write_text(toml(selected))
    (root / "version.txt").write_text("0.1.0\n")
    (root / "uv.lock").write_text(
        'version = 1\nrevision = 5\nrequires-python = ">=3.15,<3.16"\n[[package]]\nname = "jellyfin-plugin-toolkit"\nversion = "0.2.0"\nsource = { url = "https://github.com/rakuzen25/jellyfin-plugin-toolkit/releases/download/v0.2.0/jellyfin_plugin_toolkit-0.2.0-py3-none-any.whl" }\nwheels = [{ hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }]\n'.replace(
            "0.2.0", package_version()
        )
    )
    (root / "frontend/admin").mkdir(parents=True, exist_ok=True)
    (root / "frontend/admin/config.ts").write_text("export {};\n")
    (root / "frontend/build.mjs").write_text("export {};\n")
    return Context.load(root)


class ConsumerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="toolkit-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ctx = create_consumer(self.root)
        environment = patch.dict(os.environ, {"JELLYFIN_PLUGIN_ROOT": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
