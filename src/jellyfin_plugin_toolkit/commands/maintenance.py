"""Public maintenance commands and plugin release operations."""

from pathlib import Path
from typing import Annotated

from jellyfin_plugin_toolkit.commands.runtime import (
    application,
    exclusive,
    state,
    typer,
)

editor = application(help="Configure editor integrations.")
toolkit = application(help="Maintain native toolkit dependency and resource bindings.")
release_app = application(
    help="Inspect plugin release metadata and attach verified draft artifacts."
)
automation = application(help="Configure the connected repository App.")


@editor.command()
def vscode(ctx: typer.Context, apply: Annotated[bool, typer.Option("--apply")] = False):
    """Preview or apply VS Code settings while preserving unrelated settings."""
    from jellyfin_plugin_toolkit.maintenance import editor as configure

    configure(state(ctx).root, apply)


@toolkit.command()
def sync(
    ctx: typer.Context,
    base_ref: str | None = None,
    apply: Annotated[bool, typer.Option("--apply")] = False,
    check: Annotated[bool, typer.Option("--check")] = False,
):
    """Preview, check or apply native toolkit synchronization."""
    exclusive(**{"--check": check, "--apply": apply})
    from jellyfin_plugin_toolkit import bindings

    root = state(ctx).root
    changes = bindings.plan(root, base_ref)
    bindings.preview(changes)
    if check and changes:
        raise ValueError("Toolkit bindings are stale")
    if apply:
        bindings.apply(root, changes)


@toolkit.command()
def recover(ctx: typer.Context):
    """Recover an interrupted synchronization without discarding later edits."""
    from jellyfin_plugin_toolkit.bindings import recover as restore

    restore(state(ctx).root)


@automation.command()
def setup(
    ctx: typer.Context,
    repository: Annotated[str, typer.Option()],
    retry_closed: Annotated[bool, typer.Option("--retry-closed")] = False,
):
    """Open or update the reviewed configuration PR using the connected App."""
    from jellyfin_plugin_toolkit.automation_setup import setup as configure

    configure(state(ctx).root, repository, retry_closed)


@release_app.command("config")
def release_config(
    ctx: typer.Context,
    apply: Annotated[bool, typer.Option("--apply")] = False,
    check: Annotated[bool, typer.Option("--check")] = False,
):
    """Preview, check or write Release Please rules."""
    exclusive(**{"--check": check, "--apply": apply})
    from jellyfin_plugin_toolkit.release.configuration import configure

    configure(state(ctx).root, apply=apply, verify=check)


@release_app.command("check")
def release_check(ctx: typer.Context, tag: str = ""):
    """Verify plugin version, changelog and package metadata consistency."""
    from jellyfin_plugin_toolkit.release.service import check_identity

    typer.echo("Validated release identity: " + check_identity(state(ctx).root, tag))


@release_app.command()
def prepare(
    ctx: typer.Context,
    repository: Annotated[str, typer.Option()],
    environment: Annotated[Path, typer.Option()],
    tag: str = "",
):
    """Resolve release validation settings into the workflow environment file."""
    from jellyfin_plugin_toolkit.release.service import prepare as resolve

    resolve(state(ctx).root, repository, tag, environment)


@release_app.command()
def resume_draft(
    ctx: typer.Context,
    repository: Annotated[str, typer.Option()],
    environment: Annotated[Path, typer.Option()],
):
    """Resume an existing matching unpublished plugin draft."""
    from jellyfin_plugin_toolkit.release.service import resume_draft as resume

    resume(state(ctx).root, repository, environment)


@release_app.command()
def upload(
    ctx: typer.Context,
    repository: Annotated[str, typer.Option()],
    tag: Annotated[str, typer.Option()],
    directory: Path = Path("dist"),
):
    """Verify and attach tested assets to an existing draft; never publish."""
    from jellyfin_plugin_toolkit.release.service import upload as attach

    root = state(ctx).root
    attach(root, root / directory, repository, tag)
