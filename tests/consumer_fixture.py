"""Synthetic consumer context for generic tooling tests; no pilot repository imports."""

import atexit
import json
import os
import tempfile
from pathlib import Path

from jellyfin_plugin_toolkit.scaffold import toml

temporary = tempfile.TemporaryDirectory(prefix="toolkit-tests-")
atexit.register(temporary.cleanup)
ROOT = Path(temporary.name)
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
(ROOT / "plugin.toml").write_text(toml(config))
(ROOT / "version.txt").write_text("0.1.0\n")
(ROOT / "toolkit.lock.json").write_text(
    json.dumps(
        {
            "schema": 1,
            "repository": "example/toolkit",
            "version": "v1.0.0",
            "sha256": "a" * 64,
        }
    )
)
os.environ["JELLYFIN_PLUGIN_ROOT"] = str(ROOT)
