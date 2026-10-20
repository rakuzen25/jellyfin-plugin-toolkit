"""Independent synthetic consumer context for generic tooling tests."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.scaffold import toml

defaults = json.loads(
    (Path(__file__).resolve().parents[1] / "templates/defaults.json").read_text()
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
config = {
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
            "toolkit.lock.json",
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


def create_consumer(root, selected=None):
    selected = copy.deepcopy(config if selected is None else selected)
    (root / "plugin.toml").write_text(toml(selected))
    (root / "version.txt").write_text("0.1.0\n")
    (root / "toolkit.lock.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "repository": "example/toolkit",
                "version": "v1.0.0",
                "sha256": "a" * 64,
            }
        )
    )
    (root / "frontend/admin").mkdir(parents=True, exist_ok=True)
    (root / "frontend/admin/config.ts").write_text("export {};\n")
    (root / "frontend/build.mjs").write_text("export {};\n")
    return Context.load(root)


class ConsumerTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="toolkit-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ctx = create_consumer(self.root)
        environment = patch.dict(os.environ, {"JELLYFIN_PLUGIN_ROOT": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
