"""Fixture-only browser test identity, independent of unrelated build inputs."""

import fnmatch
import hashlib
import json
import os
import re

from jellyfin_plugin_toolkit.common.artifacts import digest
from jellyfin_plugin_toolkit.common.source_inventory import (
    frontend_inputs,
    source_files,
)
from jellyfin_plugin_toolkit.context import output_name


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_inputs(ctx, spec, maintained):
    selected = set()
    for pattern in spec.files:
        matches = {
            path for path in maintained if fnmatch.fnmatchcase(path.as_posix(), pattern)
        }
        if not matches:
            raise ValueError(
                "Browser input pattern matches no maintained files: " + pattern
            )
        selected.update(matches)
    return digest(ctx.root, selected)


def settings(ctx, group, spec):
    return {
        "group": group,
        "projects": spec.projects,
        "workers": spec.workers,
        "runner": spec.runner,
        "image": ctx.images["playwright"],
        "command": ctx.config.browser.command,
        "browser": ctx.config.browser.model_dump(exclude_none=True),
        "assets": spec.assets,
    }


def capture(ctx):
    if not ctx.config.validation.browser_inputs:
        return {}
    maintained = source_files(ctx.root)
    source = digest(ctx.root, frontend_inputs(ctx, maintained))
    files = {}
    for path in sorted((ctx.root / "dist/web").rglob("*")):
        if path.is_symlink():
            raise ValueError("Browser assets cannot contain symlinks")
        if path.is_file():
            files[path.relative_to(ctx.root / "dist/web").as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    result = {}
    for group, spec in ctx.config.validation.browser_inputs.items():
        assets = {}
        for pattern in spec.assets:
            matched = {
                name: value
                for name, value in files.items()
                if fnmatch.fnmatchcase(name, pattern)
            }
            if not matched:
                raise ValueError(
                    "Browser asset pattern matches no built files: " + pattern
                )
            assets.update(matched)
        result[group] = {
            "source": source,
            "tests": test_inputs(ctx, spec, maintained),
            "assets": assets,
            "settings": settings(ctx, group, spec),
            "runner_image": {
                key: os.environ.get(key, "")
                for key in ("ImageOS", "ImageVersion", "RUNNER_ARCH", "RUNNER_OS")
            },
        }
    return result


def verify(ctx, proofs):
    """Validate a trusted receipt without rebuilding assets in the publication job."""
    specs = ctx.config.validation.browser_inputs
    if not isinstance(proofs, dict) or set(proofs) != set(specs):
        raise ValueError("Browser evidence does not match configured groups")
    if not specs:
        return {}
    maintained = source_files(ctx.root)
    source = digest(ctx.root, frontend_inputs(ctx, maintained))
    results = {}
    for group, spec in specs.items():
        proof = proofs[group]
        if not isinstance(proof, dict) or set(proof) != {
            "source",
            "tests",
            "assets",
            "settings",
            "runner_image",
        }:
            raise ValueError("Malformed browser input evidence")
        if (
            proof["source"] != source
            or proof["tests"] != test_inputs(ctx, spec, maintained)
            or proof["settings"] != settings(ctx, group, spec)
        ):
            raise ValueError("Browser source or execution inputs changed")
        assets = proof["assets"]
        if not isinstance(assets, dict) or not assets:
            raise ValueError("Browser evidence has no built assets")
        for name, value in assets.items():
            output_name(name)
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("Invalid browser asset hash")
        if any(
            not any(fnmatch.fnmatchcase(name, pattern) for name in assets)
            for pattern in spec.assets
        ) or any(
            not any(fnmatch.fnmatchcase(name, pattern) for pattern in spec.assets)
            for name in assets
        ):
            raise ValueError("Browser asset inventory does not match configuration")
        runner = proof["runner_image"]
        if (
            not isinstance(runner, dict)
            or set(runner) != {"ImageOS", "ImageVersion", "RUNNER_ARCH", "RUNNER_OS"}
            or any(not isinstance(value, str) for value in runner.values())
        ):
            raise ValueError("Invalid browser runner identity")
        # Source provenance is verified above, but emitted bytes determine reuse.
        results[group] = fingerprint(
            {key: value for key, value in proof.items() if key != "source"}
        )
    return results


def verify_execution(ctx, selection, group, project, workers):
    spec = ctx.config.validation.browser_inputs.get(group)
    if spec is None:
        return
    if project not in spec.projects or workers != spec.workers:
        raise ValueError("Browser execution differs from the selected projects/workers")
    expected = selection.get("browser", {}).get(group)
    actual = capture(ctx).get(group)
    if expected is None or expected != actual:
        raise ValueError(
            "Built browser assets or runner differ from the validation plan"
        )
