"""Build distributions from a captured source inventory and the exact toolkit archive."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jellyfin_plugin_toolkit.common.artifacts import artifact_hashes, digest, verify_web
from jellyfin_plugin_toolkit.common.source_inventory import source_files


def clean_package(ctx, release=False, prepared_web=False):
    repository = ctx.root
    files = source_files(repository)
    captured = digest(repository, files)
    lock = json.loads((repository / "toolkit.lock.json").read_text())
    archived = repository / ".build/toolkit" / (lock["sha256"] + ".tar.gz")
    if prepared_web:
        verify_web(repository)
    with tempfile.TemporaryDirectory(prefix="jellyfin-plugin-clean-") as temporary:
        root = Path(temporary)
        for name in files:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository / name, destination)
        (root / "build-support").mkdir(exist_ok=True)
        shutil.copy2(archived, root / "build-support/toolkit.tar.gz")
        if prepared_web:
            shutil.copytree(repository / "dist/web", root / "dist/web")
            shutil.copy2(
                repository / "dist/web-build.json", root / "dist/web-build.json"
            )
        environment = {
            **os.environ,
            "JELLYFIN_PLUGIN_ROOT": str(root),
            "JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE": str(archived),
            "JELLYFIN_PLUGIN_REFERENCE_DIR": str(
                Path(
                    os.environ.get(
                        "JELLYFIN_PLUGIN_REFERENCE_DIR", repository / ".build/jellyfin"
                    )
                ).resolve()
            ),
        }
        subprocess.run(
            [sys.executable, str(root / "plugin"), "_package"]
            + (["--release"] if release else [])
            + (["--prepared-web"] if prepared_web else []),
            cwd=root,
            env=environment,
            check=True,
        )
        if source_files(repository) != files or digest(repository, files) != captured:
            raise ValueError("Source changed during clean build; rerun ./plugin build")
        out = repository / "dist"
        out.mkdir(exist_ok=True)
        products = artifact_hashes(root)
        previous = [p for p in out.glob("*.zip") if p.name not in products]
        if previous:
            archive = Path(tempfile.mkdtemp(prefix="previous-", dir=out))
            for p in previous:
                shutil.move(p, archive / p.name)
        for name in products:
            shutil.copy2(root / "dist" / name, out / name)
        if not prepared_web and (root / "dist/web").exists():
            shutil.copytree(root / "dist/web", out / "web", dirs_exist_ok=True)
        (out / "clean-build.json").write_text(
            json.dumps(
                {"source": captured, "toolkit": lock, "artifacts": products}, indent=2
            )
            + "\n"
        )
