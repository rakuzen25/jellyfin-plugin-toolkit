"""One typed command tree for developers and trusted automation."""

import json
import os
import subprocess
import sys
import traceback
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from jellyfin_plugin_toolkit.commands.internal import internal
from jellyfin_plugin_toolkit.commands.maintenance import (
    automation,
    editor,
    release_app,
    toolkit,
)
from jellyfin_plugin_toolkit.commands.runtime import (
    State,
    application,
    consumer,
    current,
    exclusive,
    state,
    typer,
)

app = application(help="Build, develop, and validate independent Jellyfin plugins.")


def version(value: bool):
    if value:
        from jellyfin_plugin_toolkit.provenance import package_version

        typer.echo(package_version())
        raise typer.Exit()


@app.callback()
def configure(
    ctx: typer.Context,
    root: Annotated[
        Path | None,
        typer.Option(envvar="JELLYFIN_PLUGIN_ROOT", help="Consumer project root."),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug", help="Show failure tracebacks without local variables."
        ),
    ] = False,
    show_version: Annotated[
        bool, typer.Option("--version", callback=version, is_eager=True)
    ] = False,
):
    selected = State((root or Path.cwd()).resolve(), debug)
    ctx.obj = selected
    current.set(selected)


@app.command()
def bootstrap(ctx: typer.Context):
    """Prepare Node, .NET, Jellyfin and shared resources."""
    from jellyfin_plugin_toolkit import tasks

    tasks.bootstrap(consumer(ctx, warn=True))


@app.command()
def doctor(ctx: typer.Context):
    """Verify tooling and print project identity as JSON."""
    from jellyfin_plugin_toolkit import tasks

    selected = consumer(ctx, warn=True)
    tasks.verify_tools(selected)
    typer.echo(
        json.dumps(
            {
                "plugin": selected.identity.id,
                "version": selected.version,
                "root": str(selected.root),
                "groups": selected.groups,
            },
            indent=2,
        )
    )


@app.command()
def prepare(ctx: typer.Context):
    """Expose wheel resources without installing dependencies."""
    from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT
    from jellyfin_plugin_toolkit.resources_api import prepare as export

    root = state(ctx).root
    exported = export(root)
    if output := os.environ.get("GITHUB_ENV"):
        with open(output, "a") as stream:
            stream.write(
                f"JELLYFIN_PLUGIN_ROOT={root}\nJELLYFIN_PLUGIN_TOOLKIT_ROOT={exported}\n"
            )
    os.environ["JELLYFIN_PLUGIN_TOOLKIT_ROOT"] = str(TOOLKIT_ROOT)


@app.command()
def dev(
    ctx: typer.Context,
    port: Annotated[int, typer.Option(min=1, max=65535)] = 18096,
    open_browser: Annotated[bool, typer.Option("--open")] = False,
    watch: Annotated[bool, typer.Option("--watch/--no-watch")] = True,
    media_mount: Annotated[list[str] | None, typer.Option()] = None,
    clear_media_mounts: Annotated[bool, typer.Option("--clear-media-mounts")] = False,
):
    """Build, watch and run the disposable Jellyfin development session."""
    exclusive(
        **{"--media-mount": media_mount, "--clear-media-mounts": clear_media_mounts}
    )
    from jellyfin_plugin_toolkit.dev.local_jellyfin import dev as run

    run(
        consumer(ctx, warn=True),
        port=port,
        open_browser=open_browser,
        watch=watch,
        media_mounts=[] if clear_media_mounts else media_mount,
    )


class BuildTarget(StrEnum):
    frontend = "frontend"
    backend = "backend"


@app.command()
def build(
    ctx: typer.Context,
    target: Annotated[BuildTarget | None, typer.Argument()] = None,
    release: Annotated[bool, typer.Option("--release")] = False,
    prepared_web: Annotated[bool, typer.Option("--prepared-web")] = False,
):
    """Build one component or cleanly package the complete plugin."""
    if target and (release or prepared_web):
        raise typer.BadParameter("Release options apply only to complete builds")
    selected = consumer(ctx)
    if target == BuildTarget.frontend:
        from jellyfin_plugin_toolkit.tasks import frontend_build

        frontend_build(selected)
    elif target == BuildTarget.backend:
        from jellyfin_plugin_toolkit.build.backend import publish

        publish(selected, selected.root / ".build/plugin", selected.root / "dist/web")
    else:
        from jellyfin_plugin_toolkit.build.clean_package import clean_package

        clean_package(selected, release, prepared_web)


@app.command()
def check(ctx: typer.Context, target: Annotated[str | None, typer.Argument()] = None):
    """Check one consumer-defined target or all configured targets."""
    from jellyfin_plugin_toolkit import tasks

    selected = consumer(ctx)
    for name in [target] if target else selected.config.validation.check_targets:
        tasks.check(selected, name)


@app.command("format")
def format_command(
    ctx: typer.Context, target: Annotated[str | None, typer.Argument()] = None
):
    """Format one consumer-defined target or all configured targets."""
    from jellyfin_plugin_toolkit import tasks

    selected = consumer(ctx)
    for name in [target] if target else selected.config.validation.check_targets:
        tasks.format_target(selected, name, True)


@app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False}
)
def test(ctx: typer.Context, target: Annotated[str | None, typer.Argument()] = None):
    """Run configured unit tests, or TARGET -- RUNNER_ARGUMENTS."""
    if (target and target.startswith("-")) or (
        ctx.args and (not target or ctx.args[0] != "--")
    ):
        raise typer.BadParameter(
            "Runner arguments require an explicit target and -- separator"
        )
    extra = ctx.args[1:] if ctx.args else []
    from jellyfin_plugin_toolkit import tasks

    selected = consumer(ctx)
    for name in [target] if target else selected.config.validation.unit_groups:
        tasks.test(selected, name, extra)


@app.command()
def validate(ctx: typer.Context):
    """Run all local checks, unit tests, packaging and acceptance tests."""
    from jellyfin_plugin_toolkit import tasks
    from jellyfin_plugin_toolkit.build.clean_package import clean_package

    selected = consumer(ctx)
    for target in selected.config.validation.check_targets:
        tasks.check(selected, target)
    for target in selected.config.validation.unit_groups:
        tasks.test(selected, target, [])
    clean_package(selected)
    for target in selected.config.validation.acceptance_groups:
        tasks.test(selected, target, [])


@app.command()
def new(
    destination: Path,
    identifier: Annotated[str, typer.Option("--id")],
    name: Annotated[str, typer.Option()],
    repository: Annotated[str, typer.Option()],
    admin: Annotated[bool, typer.Option("--admin")] = False,
    sync_workflow: Annotated[bool, typer.Option("--sync-workflow")] = False,
    host_project: str = "src/Host/Plugin.csproj",
    toolkit_wheel: Path | None = None,
    toolkit_revision: str | None = None,
):
    """Generate an independent uv consumer without App credentials."""
    from jellyfin_plugin_toolkit.scaffold import generate

    generate(
        None,
        destination.resolve(),
        identifier,
        name,
        repository,
        admin,
        host_project,
        toolkit_wheel=toolkit_wheel,
        toolkit_revision=toolkit_revision,
        sync_workflow=sync_workflow,
    )


@app.command()
def paths(ctx: typer.Context):
    """Print editor and toolkit paths as JSON without checking bindings."""
    from jellyfin_plugin_toolkit.maintenance import paths as inspect

    typer.echo(json.dumps(inspect(state(ctx).root), indent=2))


app.add_typer(editor, name="editor")
app.add_typer(toolkit, name="toolkit")
app.add_typer(release_app, name="release")
app.add_typer(automation, name="automation")
app.add_typer(internal, name="internal", hidden=True)


def main(argv=None):
    """Shared console/module boundary with stable status and diagnostic handling."""
    token = current.set(None)
    exported = {
        name: os.environ.get(name)
        for name in ["JELLYFIN_PLUGIN_ROOT", "JELLYFIN_PLUGIN_TOOLKIT_ROOT"]
    }
    try:
        result = app(args=argv, prog_name="jpt", standalone_mode=False)
        return result if isinstance(result, int) else 0
    except typer.Exit as error:
        return error.exit_code
    except SystemExit as error:
        # Typer shell completion exits directly even in non-standalone mode.
        return error.code if isinstance(error.code, int) else 1
    except typer.Abort, KeyboardInterrupt:
        typer.echo("Aborted.", err=True)
        return 130
    except typer.TyperException as error:
        error.show()
        return error.exit_code
    except Exception as error:
        selected = current.get()
        if selected and selected.debug:
            traceback.print_exc(file=sys.stderr)
        elif isinstance(error, subprocess.CalledProcessError):
            typer.echo(
                f"Error: subprocess failed with exit status {error.returncode}",
                err=True,
            )
        elif isinstance(error, (ValueError, OSError)):
            typer.echo("Error: " + str(error), err=True)
        else:
            typer.echo(
                f"Error: {type(error).__name__}; use --debug for a traceback", err=True
            )
        if isinstance(error, subprocess.CalledProcessError):
            return (
                error.returncode
                if 0 < error.returncode < 256
                else min(255, 128 - error.returncode)
                if error.returncode < 0
                else 1
            )
        return 1
    finally:
        current.reset(token)
        for name, value in exported.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
