"""Locked toolkit provenance cannot redirect corresponding-source writes."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from consumer_fixture import ConsumerTestCase

from jellyfin_plugin_toolkit import provenance


class ProvenanceTests(ConsumerTestCase):
    def test_malformed_version_cannot_be_used_as_a_distribution_path(self):
        lock = self.root / "uv.lock"
        text = lock.read_text()
        current = provenance.package_version()
        lock.write_text(
            text.replace(f'version = "{current}"', 'version = "../../outside"')
        )
        with self.assertRaisesRegex(ValueError, "stable release version"):
            provenance.record(self.root)

    def test_remote_dependencies_require_canonical_released_wheels(self):
        lock = self.root / "uv.lock"
        lock.write_text(
            lock.read_text().replace("https://github.com/", "https://attacker.invalid/")
        )
        with self.assertRaisesRegex(ValueError, "canonical GitHub"):
            provenance.record(self.root)

    def test_source_bundle_directory_cannot_redirect_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            (self.root / "build-support").symlink_to(folder, target_is_directory=True)
            with patch.object(provenance.urllib.request, "urlopen") as request:
                with self.assertRaises(ValueError):
                    provenance.distributions(self.root)
            request.assert_not_called()
            self.assertEqual([], list(Path(folder).iterdir()))

    def test_distribution_file_cannot_redirect_writes(self):
        support = self.root / "build-support"
        support.mkdir()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "outside"
            (support / provenance.wheel_name()).symlink_to(target)
            with patch.object(provenance.urllib.request, "urlopen") as request:
                with self.assertRaises(ValueError):
                    provenance.distributions(self.root)
            request.assert_not_called()
            self.assertFalse(target.exists())
