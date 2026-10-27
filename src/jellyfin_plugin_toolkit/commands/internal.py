"""Typed adapters for CI receipts, sandbox transport and release-bot helpers."""

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from jellyfin_plugin_toolkit.commands.runtime import (
    application,
    consumer,
    exclusive,
    state,
)

internal = application(
    help="Implementation helpers used by trusted workflows and subprocesses."
)
ci = application(help="Plan and authenticate GitHub-based validation evidence.")
binding_pr = application(
    help="Complete bounded Renovate toolkit PRs with trusted code."
)
toolkit_release = application(
    help="Build manifests and attach tested toolkit distributions."
)
sandbox = application(help="Transport operations for the Node development session.")
internal.add_typer(ci, name="ci")
internal.add_typer(binding_pr, name="binding-pr")
internal.add_typer(toolkit_release, name="toolkit-release")
internal.add_typer(sandbox, name="sandbox")


@internal.command("package")
def package(
    ctx: typer.Context,
    release: Annotated[bool, typer.Option("--release")] = False,
    prepared_web: Annotated[bool, typer.Option("--prepared-web")] = False,
) -> None:
    """Assemble a package inside an already captured clean source checkout."""
    from jellyfin_plugin_toolkit.build.package import package as assemble

    assemble(consumer(ctx), release, prepared_web)


@sandbox.command()
def deploy(
    ctx: typer.Context,
    web: Annotated[Path, typer.Option()],
    media_mount: Annotated[list[str] | None, typer.Option()] = None,
    clear_media_mounts: Annotated[bool, typer.Option("--clear-media-mounts")] = False,
) -> None:
    """Deploy a successful build into the owned sandbox."""
    exclusive(
        **{"--media-mount": media_mount, "--clear-media-mounts": clear_media_mounts}
    )
    from jellyfin_plugin_toolkit.context import Context
    from jellyfin_plugin_toolkit.dev.local_jellyfin import deploy as install

    root = state(ctx).root
    install(
        Context.load(root),
        (root / web).resolve(),
        [] if clear_media_mounts else media_mount,
    )


@sandbox.command()
def stop(ctx: typer.Context) -> None:
    """Stop the owned sandbox during session shutdown."""
    from jellyfin_plugin_toolkit.context import Context
    from jellyfin_plugin_toolkit.dev.local_jellyfin import stop as shutdown

    shutdown(Context.load(state(ctx).root))


@ci.command()
def plan(
    ctx: typer.Context,
    base: str = "",
    full: Annotated[bool, typer.Option("--full")] = False,
    release: Annotated[bool, typer.Option("--release")] = False,
    reuse: Annotated[bool, typer.Option("--reuse")] = False,
    scheduled: Annotated[bool, typer.Option("--scheduled")] = False,
) -> None:
    """Select checks and reuse authenticated evidence where inputs match."""
    from jellyfin_plugin_toolkit.ci.execution import plan as select

    select(
        consumer(ctx),
        base=base,
        full=full,
        release=release,
        reuse=reuse,
        scheduled=scheduled,
    )


@ci.command()
def refine(ctx: typer.Context) -> None:
    """Resolve browser reuse after the selected frontend build."""
    from jellyfin_plugin_toolkit.ci.execution import refine as resolve

    resolve(consumer(ctx))


@internal.command("renovate-test")
def renovate_test(ctx: typer.Context, script: Path) -> None:
    """Run repository assertions with the toolkit-owned Renovate test runtime."""
    from jellyfin_plugin_toolkit.resources_api import prepare
    from jellyfin_plugin_toolkit.validators import renovate_test as execute

    root = state(ctx).root
    prepare(root)
    execute(root, script)


@internal.command("audit-selection")
def audit_selection(
    ctx: typer.Context, fresh: Annotated[bool, typer.Option("--fresh")] = False
) -> None:
    """Select dependency-aware standalone audits from GitHub event data."""
    from jellyfin_plugin_toolkit.audit_policy import select

    select(state(ctx).root, fresh=fresh)


@ci.command()
def finish(ctx: typer.Context) -> None:
    """Write the aggregate validation receipt from completed job results."""
    from jellyfin_plugin_toolkit.ci.execution import finish as aggregate

    aggregate(consumer(ctx))


@ci.command()
def release_receipt(
    ctx: typer.Context, run_id: Annotated[int, typer.Option(min=1)]
) -> None:
    """Authenticate CI evidence and stage the public release receipt and assets."""
    from jellyfin_plugin_toolkit.ci.execution import release_receipt as authenticate

    authenticate(consumer(ctx, release=True), str(run_id))


@ci.command()
def metadata_check(ctx: typer.Context) -> None:
    """Validate automation metadata and immutable toolkit/workflow pins."""
    from jellyfin_plugin_toolkit.ci.policy import metadata_check as inspect

    inspect(consumer(ctx))


@ci.command()
def browser_verify(
    ctx: typer.Context,
    group: Annotated[str, typer.Option()],
    project: Annotated[str, typer.Option()],
    workers: Annotated[int, typer.Option(min=1)],
) -> None:
    """Verify that the browser invocation matches the consumed-input fingerprint."""
    from jellyfin_plugin_toolkit.ci.browser import verify_execution

    selected = consumer(ctx)
    verify_execution(
        selected,
        json.loads((selected.root / "dist/plan.json").read_text()),
        group,
        project,
        workers,
    )


@ci.command()
def web_record(ctx: typer.Context) -> None:
    """Record the exact emitted browser assets."""
    from jellyfin_plugin_toolkit.ci.execution import web_record as record

    record(consumer(ctx))


@ci.command()
def web_verify(ctx: typer.Context) -> None:
    """Verify the prepared browser-asset inventory."""
    from jellyfin_plugin_toolkit.common.artifacts import verify_web

    verify_web(consumer(ctx).root)


@binding_pr.command("prepare")
def prepare_bindings(
    ctx: typer.Context,
    proposal: Annotated[Path, typer.Option()],
    repository: Annotated[str, typer.Option(envvar="GITHUB_REPOSITORY")],
    bot: str = "renovate[bot]",
    run_id: Annotated[int | None, typer.Option(min=1)] = None,
    pr: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Prepare a data-only update proposal from trusted base code."""
    exclusive(**{"--run-id": run_id, "--pr": pr})
    if not (run_id or pr):
        raise typer.BadParameter("Prepare requires --pr or --run-id")
    from jellyfin_plugin_toolkit import binding_pr as service

    root = state(ctx).root
    request = service.discover(repository, bot, run_id, pr)
    result = service.prepare(root, repository, request, bot) if request else None
    ready = result is not None and bool(result["changes"])
    if result:
        (root / proposal).write_text(json.dumps(result, indent=2) + "\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.write(f"ready={str(ready).lower()}\n")
            if result:
                stream.write("base=" + result["base"] + "\n")
    typer.echo(
        "Prepared toolkit binding update."
        if ready
        else "No eligible toolkit binding changes."
    )


@binding_pr.command("commit")
def commit_bindings(
    ctx: typer.Context,
    proposal: Annotated[Path, typer.Option()],
    repository: Annotated[str, typer.Option(envvar="GITHUB_REPOSITORY")],
    bot: str = "renovate[bot]",
) -> None:
    """Recompute and commit a bounded proposal using the existing App."""
    from jellyfin_plugin_toolkit.binding_pr import commit

    root = state(ctx).root
    commit(root, repository, json.loads((root / proposal).read_text()), bot)


@toolkit_release.command("manifest")
def toolkit_manifest(
    ctx: typer.Context,
    sha: Annotated[str, typer.Option()],
    directory: Path = Path("dist"),
) -> None:
    """Record distribution hashes and source provenance."""
    from jellyfin_plugin_toolkit.toolkit_release import manifest

    manifest(state(ctx).root / directory, sha)


@toolkit_release.command("resolve")
def toolkit_resolve(
    ctx: typer.Context,
    tag: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
    repository: Annotated[str, typer.Option(envvar="GITHUB_REPOSITORY")],
    sha: str = "",
) -> None:
    """Resolve an existing toolkit draft without publishing it."""
    from jellyfin_plugin_toolkit.toolkit_release import resolve

    _, revision = resolve(repository, tag, sha)
    with (state(ctx).root / output).open("a") as stream:
        stream.write(f"tag={tag}\nsha={revision}\n")


@toolkit_release.command("upload")
def toolkit_upload(
    ctx: typer.Context,
    tag: Annotated[str, typer.Option()],
    sha: Annotated[str, typer.Option()],
    repository: Annotated[str, typer.Option(envvar="GITHUB_REPOSITORY")],
    directory: Path = Path("dist"),
) -> None:
    """Attach the exact tested wheel and source distribution to the draft."""
    from jellyfin_plugin_toolkit.toolkit_release import upload

    upload(state(ctx).root / directory, repository, tag, sha)


@toolkit_release.command("select-validation")
def toolkit_select_validation(
    sha: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
    repository: Annotated[str, typer.Option(envvar="GITHUB_REPOSITORY")],
    retry: Annotated[bool, typer.Option("--retry")] = False,
) -> None:
    """Wait for and authenticate the regular CI candidate for a release commit."""
    from jellyfin_plugin_toolkit.toolkit_validation import select

    selected = select(repository, sha, retry=retry)
    with output.open("a") as stream:
        stream.write(f"needs_validation={'false' if selected else 'true'}\n")
        if selected:
            stream.write(
                f"run_id={selected.run_id}\nartifact_id={selected.artifact_id}\n"
            )


@toolkit_release.command("validation-receipt")
def toolkit_validation_receipt(
    sha: Annotated[str, typer.Option()],
    artifact_id: Annotated[int, typer.Option(min=1)],
    run_id: Annotated[int, typer.Option(envvar="GITHUB_RUN_ID", min=1)],
    run_attempt: Annotated[int, typer.Option(envvar="GITHUB_RUN_ATTEMPT", min=1)],
    repository: Annotated[str, typer.Option(envvar="GITHUB_REPOSITORY")],
    results: Annotated[str, typer.Option(envvar="VALIDATION_RESULTS")],
    directory: Path = Path(".build/validation"),
) -> None:
    """Bind successful validation to the exact candidate used by starter tests."""
    from jellyfin_plugin_toolkit.toolkit_validation import record

    record(
        directory,
        repository,
        sha,
        run_id,
        run_attempt,
        artifact_id,
        json.loads(results),
    )


@internal.command()
def references(ctx: typer.Context) -> None:
    """Restore and verify the official Jellyfin reference assemblies."""
    from jellyfin_plugin_toolkit.context import Context
    from jellyfin_plugin_toolkit.dev.references import ensure

    ensure(Context.load(state(ctx).root))


@internal.command()
def restore_backend(ctx: typer.Context) -> None:
    """Restore the pinned backend dependencies."""
    from jellyfin_plugin_toolkit.build.backend import prepare

    prepare(consumer(ctx))


@internal.command()
def architecture(ctx: typer.Context) -> None:
    """Check declared project boundaries."""
    from jellyfin_plugin_toolkit.checks.architecture import check_projects

    check_projects(consumer(ctx).root)
    typer.echo("PASS: declared project graph and host-independent boundaries")


@internal.command()
def reproducibility(ctx: typer.Context) -> None:
    """Independently rebuild the captured source distribution."""
    from jellyfin_plugin_toolkit.reproducibility import main as verify

    verify(consumer(ctx))


@internal.command()
def installation(ctx: typer.Context) -> None:
    """Exercise package installation in the disposable acceptance environment."""
    from jellyfin_plugin_toolkit.installation import main as verify

    verify(consumer(ctx))


@internal.command()
def docs(ctx: typer.Context) -> None:
    """Check documentation links and README reachability."""
    from jellyfin_plugin_toolkit.checks.docs import main as inspect

    inspect(consumer(ctx))


@internal.command()
def tooling(ctx: typer.Context) -> None:
    """Check tooling quality and workflow consistency."""
    from jellyfin_plugin_toolkit.checks.formatting import format_target
    from jellyfin_plugin_toolkit.checks.tooling import main as inspect

    selected = consumer(ctx)
    format_target(selected, "tooling", False)
    inspect(selected)
