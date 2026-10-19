import tempfile
import unittest
from pathlib import Path

from jellyfin_plugin_toolkit.context import Context, relative


class ConfigurationTests(unittest.TestCase):
    def test_paths_cannot_escape_consumer_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["../secret", "/tmp/secret"]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    relative(root, name)

    def test_consumer_root_is_not_the_toolkit_installation(self):
        from jellyfin_plugin_toolkit.scaffold import generate

        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "consumer"
            generate(None, selected, "sample", "Sample", "example/sample")
            context = Context.load(selected)
            self.assertEqual(selected.resolve(), context.root)
            self.assertNotEqual(Path(__file__).resolve().parents[1], context.root)
