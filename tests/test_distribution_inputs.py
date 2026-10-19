"""Evidence includes nested frontend bytes and preserves external version inputs."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from consumer_fixture import config

from jellyfin_plugin_toolkit.common.artifacts import digest, web_inputs
from jellyfin_plugin_toolkit.scaffold import toml


class DistributionInputTests(unittest.TestCase):
    def fixture(self, root):
        selected = copy.deepcopy(config)
        selected["backend"]["project_assemblies"] = ["Example.Core"]
        (root / "plugin.toml").write_text(toml(selected))
        (root / "version.txt").write_text("0.1.0\n")
        (root / "toolkit.lock.json").write_text("{}")

    def test_nested_asset_mutation_invalidates_web_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            nested = root / "dist/web/icons/mark.svg"
            nested.parent.mkdir(parents=True)
            nested.write_text("original")
            first = web_inputs(root)
            self.assertIn("icons/mark.svg", first["files"])
            nested.write_text("changed")
            self.assertNotEqual(first, web_inputs(root))

    def test_version_normalization_applies_only_to_owned_project_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            path = Path("packages.lock.json")
            lock = {
                "dependencies": {
                    "net10.0": {
                        "Host": {
                            "type": "Project",
                            "dependencies": {
                                "Example.Core": "[0.1.0, )",
                                "External.Dependency": "[2.0.0, )",
                            },
                        }
                    }
                }
            }
            (root / path).write_text(json.dumps(lock))
            first = digest(root, [path], True)
            dependencies = lock["dependencies"]["net10.0"]["Host"]["dependencies"]
            dependencies["Example.Core"] = "[0.2.0, )"
            (root / path).write_text(json.dumps(lock))
            self.assertEqual(first, digest(root, [path], True))
            dependencies["External.Dependency"] = "[2.1.0, )"
            (root / path).write_text(json.dumps(lock))
            self.assertNotEqual(first, digest(root, [path], True))
