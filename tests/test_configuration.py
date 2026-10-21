import copy
import json
import tempfile
import unittest
from pathlib import Path

from consumer_fixture import config

from jellyfin_plugin_toolkit.configuration import Configuration
from jellyfin_plugin_toolkit.context import Context, relative


class ConfigurationTests(unittest.TestCase):
    def test_strict_configuration_rejects_coercions_and_unknown_fields(self):
        for section, key, value in [
            ("frontend", "enabled", "true"),
            ("frontend", "enabeld", True),
            ("backend", "tests", "test.csproj"),
        ]:
            selected = copy.deepcopy(config)
            selected.setdefault(section, {})[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                Configuration.model_validate(selected)
        for value in (True, 1.0, "1"):
            with self.subTest(schema=value), self.assertRaises(ValueError):
                Configuration.model_validate({**config, "schema": value})

    def test_editor_schema_matches_runtime_models(self):
        schema = (
            Path(__file__).resolve().parents[1]
            / "src/jellyfin_plugin_toolkit/resources/configs/plugin.schema.json"
        )
        self.assertEqual(
            Configuration.model_json_schema(), json.loads(schema.read_text())
        )

    def test_toml_round_trip_preserves_nested_values_and_escaping(self):
        import tomllib

        from jellyfin_plugin_toolkit.scaffold import toml

        values = {
            "schema": 1,
            "plugin": {"name": 'Café "live"\\mix\nsecond line'},
            "operations": {"test": {"commands": [["node", "a b", "${literal}"]]}},
        }
        self.assertEqual(values, tomllib.loads(toml(values)))

    def test_paths_cannot_escape_consumer_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["../secret", "/tmp/secret"]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    relative(root, name)

    def test_consumer_root_is_not_the_toolkit_installation(self):
        from scaffold_fixture import generate

        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "consumer"
            generate(None, selected, "sample", "Sample", "example/sample")
            context = Context.load(selected)
            self.assertEqual(selected.resolve(), context.root)
            self.assertNotEqual(Path(__file__).resolve().parents[1], context.root)
