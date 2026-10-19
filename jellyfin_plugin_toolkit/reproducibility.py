"""Independently rebuild the distributed source using its included toolkit."""

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from jellyfin_plugin_toolkit.common.artifacts import artifact_hashes
from jellyfin_plugin_toolkit.context import Context


def main(ctx):
    candidate = artifact_hashes(ctx.root)
    archive = ctx.root / "dist" / f"{ctx.prefix}-source-{ctx.version}.zip"
    with tempfile.TemporaryDirectory(prefix="plugin-reproducibility-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                path = root / member.filename
                if not path.resolve().is_relative_to(
                    root
                ) or member.filename.startswith("/"):
                    raise ValueError("Unsafe source archive path")
                if member.is_dir():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(package.read(member))
                path.chmod((member.external_attr >> 16) & 0o777 or 0o644)
        environment = {
            **os.environ,
            "JELLYFIN_PLUGIN_ROOT": str(root),
            "JELLYFIN_PLUGIN_REFERENCE_DIR": str(ctx.root / ".build/jellyfin"),
        }
        environment.pop("JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE", None)
        log = ctx.root / "dist/reproducibility-comparison.log"
        with log.open("w") as output:
            subprocess.run(
                [sys.executable, str(root / "plugin"), "_package"],
                cwd=root,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
            )
        if artifact_hashes(root) != candidate:
            raise ValueError(
                "Independent distribution hashes differ; inspect " + str(log)
            )
    print(
        "PASS: independently rebuilt plugin, source, manifest and checksum bytes match"
    )


if __name__ == "__main__":
    main(Context.load())
