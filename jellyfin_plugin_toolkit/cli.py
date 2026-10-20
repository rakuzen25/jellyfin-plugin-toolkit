"""Stable consumer command surface."""

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    from jellyfin_plugin_toolkit import tasks
    from jellyfin_plugin_toolkit.context import Context

    forwarded = list(sys.argv[1:] if argv is None else argv)
    if forwarded and forwarded[0] == "toolkit":
        from jellyfin_plugin_toolkit.maintenance import main as maintenance

        return maintenance(forwarded[1:])
    if forwarded and forwarded[0] in {"ci", "release"}:
        import importlib

        module = importlib.import_module(
            "jellyfin_plugin_toolkit." + forwarded[0] + ".__main__"
        )
        module.main(forwarded[1:])
        return
    parser = argparse.ArgumentParser(prog="./plugin")
    sub = parser.add_subparsers(dest="task", required=True)
    for name in ["bootstrap", "doctor"]:
        sub.add_parser(name)
    dev = sub.add_parser("dev")
    dev.add_argument("--port", type=int, default=18096)
    dev.add_argument("--open", action="store_true")
    dev.add_argument("--no-watch", action="store_true")
    mounts = dev.add_mutually_exclusive_group()
    mounts.add_argument("--media-mount", action="append")
    mounts.add_argument("--clear-media-mounts", action="store_true")
    for name in ["build", "check", "format", "test", "_package"]:
        command = sub.add_parser(name)
        command.add_argument("target", nargs="?")
        if name in {"build", "_package"}:
            command.add_argument("--release", action="store_true")
            command.add_argument("--prepared-web", action="store_true")
    validate = sub.add_parser("validate")
    validate.add_argument("--full", action="store_true")
    new = sub.add_parser("new")
    new.add_argument("destination", type=Path)
    new.add_argument("--id", required=True)
    new.add_argument("--name", required=True)
    new.add_argument("--repository", required=True)
    new.add_argument("--admin", action="store_true")
    new.add_argument("--sync-workflow", action="store_true")
    new.add_argument("--host-project", default="src/Host/Plugin.csproj")
    args, extra = parser.parse_known_args(forwarded)
    if extra and args.task != "test":
        parser.error("unrecognized arguments: " + " ".join(extra))
    ctx = Context.load()
    from jellyfin_plugin_toolkit.checks.pins import check_pins

    if args.task in {"dev", "bootstrap", "doctor"}:
        try:
            check_pins(ctx.root)
        except ValueError as error:
            print("Toolkit warning: " + str(error), file=sys.stderr)
    else:
        check_pins(ctx.root)
    ctx.generated()
    if args.task == "bootstrap":
        tasks.bootstrap(ctx)
    elif args.task == "doctor":
        tasks.verify_tools(ctx)
        print(
            json.dumps(
                {
                    "plugin": ctx.identity.id,
                    "version": ctx.version,
                    "root": str(ctx.root),
                    "groups": ctx.groups,
                },
                indent=2,
            )
        )
    elif args.task == "dev":
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        from jellyfin_plugin_toolkit.dev.local_jellyfin import dev

        dev(ctx, args)
    elif args.task == "build":
        if args.target and (args.release or args.prepared_web):
            parser.error("release options apply only to complete builds")
        if args.target == "frontend":
            tasks.frontend_build(ctx)
        elif args.target == "backend":
            from jellyfin_plugin_toolkit.build.backend import publish

            publish(ctx, ctx.root / ".build/plugin", ctx.root / "dist/web")
        elif args.target is None:
            from jellyfin_plugin_toolkit.build.clean_package import clean_package

            clean_package(ctx, args.release, args.prepared_web)
        else:
            parser.error("unknown build target")
    elif args.task == "_package":
        from jellyfin_plugin_toolkit.build.package import package

        package(ctx, args.release, args.prepared_web)
    elif args.task == "validate":
        for target in ctx.config.validation.check_targets:
            tasks.check(ctx, target)
        for target in ctx.config.validation.unit_groups:
            tasks.test(ctx, target, [])
        from jellyfin_plugin_toolkit.build.clean_package import clean_package

        clean_package(ctx)
        for target in ctx.config.validation.acceptance_groups:
            tasks.test(ctx, target, [])
    elif args.task == "new":
        from jellyfin_plugin_toolkit.scaffold import generate

        generate(
            ctx,
            args.destination,
            args.id,
            args.name,
            args.repository,
            args.admin,
            args.host_project,
            sync_workflow=args.sync_workflow,
        )
    else:
        targets = (
            [args.target]
            if args.target
            else ctx.config.validation.unit_groups
            if args.task == "test"
            else ctx.config.validation.check_targets
        )
        for target in targets:
            if args.task == "check":
                tasks.check(ctx, target)
            elif args.task == "format":
                tasks.format_target(ctx, target, True)
            else:
                tasks.test(ctx, target, extra)
