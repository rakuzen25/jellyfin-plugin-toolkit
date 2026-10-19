import tempfile
import unittest
from pathlib import Path

from jellyfin_plugin_toolkit.context import Context
from jellyfin_plugin_toolkit.scaffold import generate


class StarterTests(unittest.TestCase):
    def test_backend_only_starter_has_no_empty_layers_or_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            generate(None, root, "example", "Example", "example/plugin")
            context = Context.load(root)
            self.assertEqual({"Host"}, set(context.config["backend"]["projects"]))
            self.assertFalse((root / "frontend").exists())
            self.assertFalse((root / "src/Core").exists())

    def test_administrator_starter_and_project_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            generate(
                None,
                root,
                "example",
                "Example",
                "example/plugin",
                True,
                "server/Entry.csproj",
            )
            context = Context.load(root)
            self.assertTrue((root / "server/Entry.csproj").exists())
            self.assertTrue(context.config["frontend"]["enabled"])
            self.assertTrue((root / "frontend/admin/config.ts").exists())
            self.assertFalse(context.config["distribution"]["web"])

    def test_existing_destination_and_escaping_project_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                generate(None, Path(directory), "example", "Example", "example/plugin")
            with self.assertRaises(ValueError):
                generate(
                    None,
                    Path(directory) / "plugin",
                    "example",
                    "Example",
                    "example/plugin",
                    False,
                    "../Outside.csproj",
                )
