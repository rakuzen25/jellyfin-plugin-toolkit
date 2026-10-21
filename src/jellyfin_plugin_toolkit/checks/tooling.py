"""Common tooling quality checks; dependency restoration is always explicit."""

from jellyfin_plugin_toolkit.checks.pins import check_pins
from jellyfin_plugin_toolkit.checks.workflows import check_workflows
from jellyfin_plugin_toolkit.common.process import executable, python_checks, run


def main(ctx):
    check_pins(ctx.root)
    roots = [name for name in ["scripts", "tests"] if (ctx.root / name).is_dir()]
    if roots:
        run(python_checks(ctx), "-m", "ruff", "check", *roots, cwd=ctx.root)
    run(executable("pnpm"), "exec", "eslint", ".", "--max-warnings", "0", cwd=ctx.root)
    check_workflows(ctx.root)
