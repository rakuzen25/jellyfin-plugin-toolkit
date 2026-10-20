"""Runtime wheels travel inside the same verified archive as toolkit source."""

import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from jellyfin_plugin_toolkit.archive import pack
from jellyfin_plugin_toolkit.bootstrap import install

ROOT = Path(__file__).resolve().parents[1]


class RuntimeBundleTests(unittest.TestCase):
    def test_archive_is_deterministic_and_runtime_is_verified_before_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            archive, checksum = pack(ROOT, folder / "first")
            duplicate, duplicate_checksum = pack(ROOT, folder / "second")
            self.assertEqual(checksum, duplicate_checksum)
            self.assertEqual(archive.read_bytes(), duplicate.read_bytes())
            with tarfile.open(archive) as package:
                names = package.getnames()
                self.assertIn("_vendor/pydantic/__init__.py", names)
                self.assertTrue(
                    any(
                        name.startswith("_vendor/pydantic_core/")
                        and name.endswith(".so")
                        for name in names
                    )
                )
                self.assertTrue(
                    any(
                        name.startswith("_vendor/tomli_w-") and name.endswith("LICENSE")
                        for name in names
                    )
                )
                self.assertFalse(any("__pycache__" in name for name in names))
            consumer = folder / "consumer"
            consumer.mkdir()
            lock = {
                "schema": 1,
                "repository": "example/toolkit",
                "version": "v" + (ROOT / "version.txt").read_text().strip(),
                "sha256": checksum,
            }
            target = install(consumer, lock, archive.read_bytes())
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys;sys.path.insert(0,sys.argv[1]);from jellyfin_plugin_toolkit.configuration import Configuration;import pydantic,tomli_w;print(pydantic.__file__)",
                    str(target),
                ],
                check=True,
                capture_output=True,
                text=True,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key != "PYTHONPATH"
                },
            )
            self.assertIn(str(target / "_vendor"), result.stdout)
            dependency = target / "_vendor/pydantic/__init__.py"
            original = dependency.read_bytes()
            dependency.write_text("raise RuntimeError('tampered dependency')")
            install(consumer, lock, archive.read_bytes())
            self.assertEqual(original, dependency.read_bytes())
