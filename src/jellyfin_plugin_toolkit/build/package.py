import tempfile
from pathlib import Path

from jellyfin_plugin_toolkit.build.backend import publish, reference_directory
from jellyfin_plugin_toolkit.build.package_artifacts import assemble
from jellyfin_plugin_toolkit.common.artifacts import verify_web
from jellyfin_plugin_toolkit.common.process import executable, run
from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT


def package(ctx, release=False, prepared_web=False):
    if release:
        from jellyfin_plugin_toolkit.checks.pins import check_pins

        check_pins(ctx.root, release=True)
    ctx.generated()
    if (ctx.root / "pnpm-lock.yaml").is_file():
        run(executable("pnpm"), "install", "--frozen-lockfile", cwd=ctx.root)
    if ctx.config.frontend.enabled:
        if prepared_web:
            verify_web(ctx.root)
        else:
            run(
                "node",
                ctx.config.frontend.build or str(TOOLKIT_ROOT / "node/build.mjs"),
                cwd=ctx.root,
            )
    with tempfile.TemporaryDirectory(prefix="jellyfin-plugin-publish-") as temporary:
        staging = Path(temporary)
        publish(
            ctx,
            staging / "plugin",
            ctx.root / "dist/web",
            references=reference_directory(ctx),
        )
        assemble(ctx, staging, release)
