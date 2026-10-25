"""Common tooling quality checks; dependency restoration is always explicit."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context


def main(ctx: Context) -> None:
    roots = [name for name in ["scripts", "tests"] if (ctx.root / name).is_dir()]
    if roots:
        from jellyfin_plugin_toolkit.quality import python

        python(ctx.root)
    from jellyfin_plugin_toolkit.quality import javascript

    exclusions: list[str] = []
    if (
        ctx.config.frontend.enabled
        and "frontend" in ctx.config.validation.check_targets
    ):
        exclusions.extend([ctx.config.frontend.directory, ctx.config.browser.directory])
    javascript(ctx.root, excludes=exclusions)
