"""Prepare and commit bounded binding updates using trusted base-release code only."""

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from jellyfin_plugin_toolkit import bindings

AUTHOR = {
    "name": "Jellyfin toolkit synchronization",
    "email": "jellyfin-plugin-toolkit-sync@users.noreply.github.com",
}


def api(repository, endpoint, method="GET", body=None):
    command = ["gh", "api", "--method", method, "repos/" + repository + "/" + endpoint]
    if body is not None:
        command += ["--input", "-"]
    output = subprocess.check_output(
        command, input=json.dumps(body).encode() if body is not None else None
    )
    return json.loads(output) if output.strip() else None


def eligible(pr, repository, bot):
    return (
        pr.get("state") == "open"
        and pr.get("user", {}).get("login") == bot
        and pr.get("user", {}).get("type") == "Bot"
        and all(
            pr.get(side, {}).get("repo", {}).get("full_name", "").lower()
            == repository.lower()
            for side in ["base", "head"]
        )
        and all(
            re.fullmatch(r"[a-f0-9]{40}", pr.get(side, {}).get("sha", ""))
            for side in ["base", "head"]
        )
    )


def discover(repository, bot, run_id=None, number=None):
    if number:
        candidates = [api(repository, "pulls/" + str(number))]
        sha = None
    else:
        run = api(repository, "actions/runs/" + str(run_id))
        if run.get("event") != "pull_request" or run.get("status") != "completed":
            return None
        sha = run.get("head_sha", "")
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            return None
        candidates = api(repository, "commits/" + sha + "/pulls")
    candidates = [
        pr
        for pr in candidates
        if eligible(pr, repository, bot) and (sha is None or pr["head"]["sha"] == sha)
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]


def snapshot(repository_root, revision, destination):
    """Materialize only binding data, never check out or execute a PR's source."""
    data = bindings.git(
        repository_root,
        "ls-tree",
        "-rz",
        "--full-tree",
        revision,
        "--",
        "plugin",
        "toolkit.lock.json",
        "renovate.json",
        ".github/workflows",
    )
    for entry in data.split(b"\0"):
        if not entry:
            continue
        details, raw_name = entry.split(b"\t", 1)
        mode, kind, _ = details.split()
        name = raw_name.decode()
        if name.startswith(".github/workflows/") and (
            Path(name).suffix not in {".yml", ".yaml"} or len(Path(name).parts) != 3
        ):
            continue
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError("Binding input must be a regular tracked file: " + name)
        contents = bindings.git(repository_root, "show", revision + ":" + name)
        if len(contents) > 2_000_000:
            raise ValueError("Binding input exceeds size limit: " + name)
        path = bindings.safe_path(destination, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        path.chmod(int(mode, 8) & 0o777)


def prepare(root, repository, pr, bot):
    if not eligible(pr, repository, bot):
        raise ValueError(
            "Only configured bot-authored, same-repository open PRs are eligible"
        )
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    if bindings.git(root, "rev-parse", "HEAD").decode().strip() != base:
        raise ValueError(
            "Base branch moved; rerun synchronization against its current revision"
        )
    token = os.environ.get("GH_TOKEN", "")
    environment = dict(os.environ)
    if token:
        environment.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic "
                + base64.b64encode(("x-access-token:" + token).encode()).decode(),
            }
        )
    subprocess.check_output(
        ["git", "-C", str(root), "fetch", "--no-tags", "origin", head], env=environment
    )
    old = json.loads(bindings.git(root, "show", base + ":toolkit.lock.json"))
    new = json.loads(bindings.git(root, "show", head + ":toolkit.lock.json"))
    if old == new:
        return None
    with tempfile.TemporaryDirectory(prefix="toolkit-pr-data-") as folder:
        destination = Path(folder)
        snapshot(root, head, destination)
        # Reuse only verified archives, never the PR's installation or environment.
        cache = destination / ".build/toolkit"
        cache.mkdir(parents=True)
        for selected in [old, new]:
            bindings.bootstrap_api()["validate_lock"](selected)
            source = bindings.safe_path(
                root, ".build/toolkit/" + selected["sha256"] + ".tar.gz"
            )
            if source.is_file():
                (cache / source.name).write_bytes(source.read_bytes())
        changes = bindings.plan(destination, base, download=True, git_root=root)
        return {
            "schema": 1,
            "repository": repository,
            "number": pr["number"],
            "base": base,
            "head": head,
            "changes": [
                {
                    "name": item.name,
                    "before": base64.b64encode(item.before).decode()
                    if item.before is not None
                    else None,
                    "after": base64.b64encode(item.after).decode(),
                    "before_mode": item.before_mode,
                    "mode": item.mode,
                }
                for item in changes
            ],
        }


def commit(root, repository, proposal, bot):
    if (
        proposal.get("schema") != 1
        or proposal.get("repository") != repository
        or type(proposal.get("number")) is not int
    ):
        raise ValueError("Invalid synchronization proposal")
    pr = api(repository, "pulls/" + str(proposal["number"]))
    # Recompute every byte with trusted code. Artifact contents are never authority.
    actual = prepare(root, repository, pr, bot)
    if actual != proposal:
        raise ValueError("PR changed or proposal was altered; rerun synchronization")
    if not proposal["changes"]:
        return
    tree = []
    for item in proposal["changes"]:
        blob = api(
            repository,
            "git/blobs",
            "POST",
            {"encoding": "base64", "content": item["after"]},
        )
        tree.append(
            {
                "path": item["name"],
                "mode": "100755" if item["mode"] == 0o755 else "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
    parent = api(repository, "git/commits/" + proposal["head"])
    new_tree = api(
        repository,
        "git/trees",
        "POST",
        {"base_tree": parent["tree"]["sha"], "tree": tree},
    )
    created = api(
        repository,
        "git/commits",
        "POST",
        {
            "message": "chore: synchronize toolkit bindings",
            "tree": new_tree["sha"],
            "parents": [proposal["head"]],
            "author": AUTHOR,
        },
    )
    latest = api(repository, "pulls/" + str(proposal["number"]))
    if not eligible(latest, repository, bot) or any(
        latest[side]["sha"] != proposal[side] for side in ["head", "base"]
    ):
        raise ValueError("PR changed before commit; rerun synchronization")
    # A normal fast-forward update also rejects a head change after the last check.
    api(
        repository,
        "git/refs/heads/" + quote(pr["head"]["ref"], safe="/"),
        "PATCH",
        {"sha": created["sha"], "force": False},
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "commit"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--bot", default="renovate[bot]")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--proposal", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare" and not (args.pr or args.run_id):
        parser.error("prepare requires --pr or --run-id")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository):
        parser.error("Expected repository owner/name")
    try:
        if args.command == "prepare":
            pr = discover(args.repository, args.bot, args.run_id, args.pr)
            proposal = prepare(args.root, args.repository, pr, args.bot) if pr else None
            ready = proposal is not None and bool(proposal["changes"])
            if proposal:
                args.proposal.write_text(json.dumps(proposal, indent=2) + "\n")
            if output := os.environ.get("GITHUB_OUTPUT"):
                with open(output, "a") as stream:
                    stream.write(f"ready={str(ready).lower()}\n")
                    if proposal:
                        stream.write("base=" + proposal["base"] + "\n")
            print(
                "Prepared toolkit binding update."
                if ready
                else "No eligible toolkit binding changes."
            )
        else:
            commit(
                args.root,
                args.repository,
                json.loads(args.proposal.read_text()),
                args.bot,
            )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        message = f"Toolkit synchronization stopped: {error}. No partial binding update was committed."
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a") as stream:
                stream.write(
                    message + "\n\nReview `./plugin toolkit sync --base-ref <base>`.\n"
                )
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    main()
