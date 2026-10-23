"""Locked toolkit provenance cannot redirect corresponding-source writes."""

import hashlib
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from consumer_fixture import ConsumerTestCase

from jellyfin_plugin_toolkit import provenance


class ProvenanceTests(ConsumerTestCase):
    def test_upgrade_uses_release_specific_checksums_and_rejects_tampering(self):
        inventories = []
        for version in ("0.4.0", "0.4.1"):
            wheel = b"wheel-" + version.encode()
            source = b"source-" + version.encode()
            files = {
                provenance.wheel_name(version): wheel,
                f"jellyfin_plugin_toolkit-{version}.tar.gz": source,
            }
            checksums = "".join(
                hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                for name, data in files.items()
            ).encode()
            downloads = {
                provenance.release_url(version, name): data
                for name, data in {**files, "SHA256SUMS": checksums}.items()
            }
            selected = {
                "version": version,
                "sha256": hashlib.sha256(wheel).hexdigest(),
                "source": provenance.release_url(version),
            }
            with (
                patch.object(provenance, "record", return_value=selected),
                patch.object(
                    provenance.urllib.request,
                    "urlopen",
                    side_effect=lambda url, **_: io.BytesIO(downloads[url]),
                ),
            ):
                distributions = provenance.distributions(self.root)
            self.assertEqual(
                [wheel, source, checksums],
                [path.read_bytes() for path in distributions],
            )
            inventories.append(distributions[-1])
        self.assertNotEqual(inventories[0], inventories[1])
        self.assertTrue(all(path.exists() for path in inventories))
        inventories[-1].write_text(
            "0" * 64 + "  " + provenance.wheel_name("0.4.1") + "\n"
        )
        with (
            patch.object(provenance, "record", return_value=selected),
            patch.object(provenance.urllib.request, "urlopen") as request,
            self.assertRaisesRegex(ValueError, "release checksums"),
        ):
            provenance.distributions(self.root)
        request.assert_not_called()

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
