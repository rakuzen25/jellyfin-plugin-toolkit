"""Common tooling quality checks; dependency restoration is always explicit."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context

from jellyfin_plugin_toolkit.checks.pins import check_pins
from jellyfin_plugin_toolkit.checks.workflows import check_workflows


def main(ctx: Context) -> None:
    check_pins(ctx.root)
    roots = [name for name in ["scripts", "tests"] if (ctx.root / name).is_dir()]
    if roots:
        from jellyfin_plugin_toolkit.quality import python

        python(ctx.root)
    from jellyfin_plugin_toolkit.quality import javascript

    javascript(ctx.root)
    check_workflows(ctx.root)
