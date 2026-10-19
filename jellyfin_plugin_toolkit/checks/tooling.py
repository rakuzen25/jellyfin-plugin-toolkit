"""Common tooling quality checks; dependency restoration is always explicit."""

from jellyfin_plugin_toolkit.checks.pins import check_pins
from jellyfin_plugin_toolkit.checks.workflows import check_workflows
from jellyfin_plugin_toolkit.common.process import ROOT, executable, python_checks, run


def main():
    from jellyfin_plugin_toolkit.tasks import format_target

    check_pins(ROOT)
    format_target("tooling", False)
    roots = [name for name in ["scripts", "tests"] if (ROOT / name).is_dir()]
    if roots:
        run(python_checks(), "-m", "ruff", "check", *roots)
    run(executable("pnpm"), "exec", "eslint", ".", "--max-warnings", "0")
    check_workflows(ROOT)


if __name__ == "__main__":
    main()
