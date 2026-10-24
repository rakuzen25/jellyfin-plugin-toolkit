"""Shared CLI state, lazy consumer preparation, and terminal presentation."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.context import Context

import os
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

# Typer reads this setting when its rendering module is first imported.
if os.environ.get("CI") or not sys.stdout.isatty():
    os.environ["TYPER_USE_RICH"] = "0"

import typer


@dataclass
class State:
    root: Path
    debug: bool = False


current: ContextVar[State | None] = ContextVar("jpt_cli_state", default=None)


def application(**options: Any) -> typer.Typer:
    return typer.Typer(pretty_exceptions_enable=False, no_args_is_help=True, **options)


def state(ctx: typer.Context) -> State:
    selected = ctx.find_object(State)
    if selected is None:
        raise ValueError("CLI state is unavailable")
    return selected


def consumer(
    ctx: typer.Context, *, warn: bool = False, release: bool = False
) -> Context:
    from jellyfin_plugin_toolkit.checks.pins import check_pins
    from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT, Context
    from jellyfin_plugin_toolkit.resources_api import prepare

    root = state(ctx).root
    selected = Context.load(root)
    try:
        check_pins(root, release=release)
    except ValueError as error:
        if not warn:
            raise
        typer.echo("Toolkit warning: " + str(error), err=True)
    prepare(root)
    os.environ["JELLYFIN_PLUGIN_ROOT"] = str(root)
    os.environ["JELLYFIN_PLUGIN_TOOLKIT_ROOT"] = str(TOOLKIT_ROOT)
    selected.generated()
    return selected


def exclusive(**values: object) -> None:
    if sum(bool(value) for value in values.values()) > 1:
        raise typer.BadParameter("Use only one of " + ", ".join(values))
