import argparse
import tempfile
from pathlib import Path

from jellyfin_plugin_toolkit.build.backend import publish, reference_directory
from jellyfin_plugin_toolkit.build.package_artifacts import assemble
from jellyfin_plugin_toolkit.common.artifacts import verify_web
from jellyfin_plugin_toolkit.common.process import executable, run
from jellyfin_plugin_toolkit.context import Context


def package(release=False, prepared_web=False):
    if release:
        from jellyfin_plugin_toolkit.checks.pins import check_pins

        check_pins(Context.load().root, release=True)
    ctx = Context.load()
    ctx.generated()
    if (ctx.root / "pnpm-lock.yaml").is_file():
        run(executable("pnpm"), "install", "--frozen-lockfile")
    if ctx.config.get("frontend", {}).get("enabled", False):
        if prepared_web:
            verify_web(ctx.root)
        else:
            run("node", ctx.config["frontend"].get("build", "frontend/build.mjs"))
    with tempfile.TemporaryDirectory(prefix="jellyfin-plugin-publish-") as temporary:
        staging = Path(temporary)
        publish(
            staging / "plugin", ctx.root / "dist/web", references=reference_directory()
        )
        assemble(staging, release)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--prepared-web", action="store_true")
    args = parser.parse_args()
    package(args.release, args.prepared_web)
