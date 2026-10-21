"""Maintain a reviewed App identity PR using trusted default-branch data."""

import base64
import copy
import json
import re
import subprocess
from urllib.parse import quote

from jellyfin_plugin_toolkit.automation import (
    STATE_PATH,
    commit_identity,
    identity_record,
    record_identity,
)
from jellyfin_plugin_toolkit.binding_pr import api

BRANCH = "jpt/automation-setup"
MARKER = "<!-- jpt-automation-setup -->"
FILES = {"renovate.json", STATE_PATH}
TITLE = "chore: configure connected GitHub App"
BODY = (
    MARKER
    + "\n\nConfigure Renovate to recognize commits from the connected GitHub App. The generated ownership record lets later App changes replace only the managed identity. Review and merge after validation passes."
)


def configuration_update(configuration, previous, identity):
    if not isinstance(configuration, dict):
        raise ValueError("Expected a Renovate JSON object")
    updated = copy.deepcopy(configuration)
    authors = updated.get("gitIgnoredAuthors", [])
    if not isinstance(authors, list) or any(
        not isinstance(item, str) for item in authors
    ):
        raise ValueError("gitIgnoredAuthors must be an array of strings")
    old = record_identity(previous)["email"] if previous is not None else None
    # Replace only the email explicitly owned by the trusted record.
    if old and old != identity["email"]:
        authors = [author for author in authors if author != old]
    if identity["email"] not in authors:
        authors.append(identity["email"])
    updated["gitIgnoredAuthors"] = authors
    return updated


def format_json(root, name, data):
    return subprocess.check_output(
        ["pnpm", "exec", "prettier", "--stdin-filepath", name],
        cwd=root,
        input=json.dumps(data).encode(),
    )


def content(repository, name, revision):
    result = api(repository, "contents/" + name + "?ref=" + revision)
    if result.get("type") != "file" or result.get("encoding") != "base64":
        raise ValueError("Setup inputs must be regular files")
    return base64.b64decode(result["content"], validate=False)


def configuration_at(repository, revision):
    tree = api(repository, "git/commits/" + revision)["tree"]["sha"]
    inventory = api(repository, "git/trees/" + tree + "?recursive=1")
    if inventory.get("truncated"):
        raise ValueError("Cannot verify setup input inventory")
    entries = {item["path"]: item for item in inventory["tree"]}
    for name in FILES & entries.keys():
        if (
            entries[name].get("mode") not in {"100644", "100755"}
            or entries[name].get("type") != "blob"
        ):
            raise ValueError("Setup inputs must be regular tracked files")
    configuration = json.loads(content(repository, "renovate.json", revision))
    record = (
        json.loads(content(repository, STATE_PATH, revision))
        if STATE_PATH in entries
        else None
    )
    return configuration, record


def branch_ref(repository):
    # matching-refs returns an empty list for an absent ref, without hiding errors.
    refs = api(repository, "git/matching-refs/heads/" + BRANCH)
    matches = [ref for ref in refs if ref["ref"] == "refs/heads/" + BRANCH]
    if len(matches) > 1:
        raise ValueError("Ambiguous setup branch")
    return matches[0]["object"]["sha"] if matches else None


def validate_branch(repository, base, head, allowed_identities, reviewed=None):
    comparison = api(repository, "compare/" + base + "..." + head)
    history = comparison
    if reviewed:
        reviewed_history = api(repository, "compare/" + reviewed + "..." + head)
        if reviewed_history["merge_base_commit"]["sha"] == reviewed:
            history = reviewed_history
    commits = history.get("commits", [])
    files = comparison.get("files", [])
    if history.get("total_commits") != len(commits) or len(files) >= 300:
        raise ValueError("Setup branch history is too large to verify")
    if any(
        item["filename"] not in FILES or item["status"] not in {"added", "modified"}
        for item in files
    ):
        raise ValueError("Setup branch contains unrelated changes")
    for item in commits:
        for role in ["author", "committer"]:
            metadata = item["commit"][role]
            identity = {"name": metadata["name"], "email": metadata["email"]}
            if (
                identity not in allowed_identities
                or (item.get(role) or {}).get("type") != "Bot"
            ):
                raise ValueError("Setup branch contains human or unrecognized commits")
    if not files:
        return comparison
    # Validate the whole proposal against its own merge base. This detects edits
    # inside the allowed JSON files while allowing main to advance independently.
    ancestor = comparison["merge_base_commit"]["sha"]
    base_configuration, previous = configuration_at(repository, ancestor)
    proposed_configuration, proposed_record = configuration_at(repository, head)
    proposed_identity = record_identity(proposed_record)
    if proposed_identity not in allowed_identities:
        raise ValueError("Setup branch proposes an unexpected App identity")
    if proposed_configuration != configuration_update(
        base_configuration, previous, proposed_identity
    ):
        raise ValueError("Setup branch configuration was edited; review it manually")
    return comparison


def setup(root, repository, retry_closed=False):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid repository identity")
    identity = commit_identity()
    record = identity_record(identity)
    details = api(repository, "")
    default = details["default_branch"]
    ref = "git/ref/heads/" + quote(default, safe="")
    base = api(repository, ref)["object"]["sha"]
    local = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    if local != base:
        raise ValueError("Default branch moved; rerun App setup")
    configuration, previous = configuration_at(repository, base)
    updated = configuration_update(configuration, previous, identity)
    if previous == record and updated == configuration:
        print("Connected App configuration is current; no writes needed")
        return
    head_query = quote(repository.split("/")[0] + ":" + BRANCH, safe="")
    pulls = api(repository, "pulls?state=all&head=" + head_query + "&per_page=100")
    if len(pulls) >= 100 or sum(pr["state"] == "open" for pr in pulls) > 1:
        raise ValueError("Ambiguous setup PR history")
    open_pulls = [pr for pr in pulls if pr["state"] == "open"]
    pr = (
        open_pulls[0]
        if open_pulls
        else max(pulls, key=lambda item: item["number"])
        if pulls
        else None
    )
    allowed = [identity]
    if previous is not None:
        old = record_identity(previous)
        if old not in allowed:
            allowed.append(old)
    if pr:
        if (
            MARKER not in (pr.get("body") or "")
            or pr["user"].get("type") != "Bot"
            or pr["user"]["login"] not in {item["name"] for item in allowed}
            or pr["base"]["ref"] != default
            or pr["head"]["repo"]["full_name"].lower() != repository.lower()
        ):
            raise ValueError("Setup PR is not owned by the configured App")
        if pr["state"] == "closed" and not pr.get("merged_at") and not retry_closed:
            print(
                "App setup PR was closed; setup remains pending. Dispatch App setup manually to retry."
            )
            return
    head = branch_ref(repository)
    merged = [
        item
        for item in pulls
        if item.get("merged_at")
        and MARKER in (item.get("body") or "")
        and item["user"].get("type") == "Bot"
        and item["base"]["ref"] == default
        and item["head"]["repo"]["full_name"].lower() == repository.lower()
    ]
    reviewed = (
        max(merged, key=lambda item: item["number"])["head"]["sha"] if merged else None
    )
    comparison = None
    if head:
        comparison = validate_branch(repository, base, head, allowed, reviewed)
        if pr and pr["head"]["sha"] != head:
            raise ValueError("Setup branch changed; rerun App setup")
    rendered = {
        "renovate.json": format_json(root, "renovate.json", updated),
        STATE_PATH: format_json(root, STATE_PATH, record),
    }
    if (
        head
        and pr
        and not pr.get("merged_at")
        and comparison["merge_base_commit"]["sha"] == base
        and all(
            content(repository, name, head) == data for name, data in rendered.items()
        )
    ):
        if pr["state"] == "closed":
            if (
                api(repository, ref)["object"]["sha"] != base
                or branch_ref(repository) != head
            ):
                raise ValueError("Setup base or branch changed; rerun App setup")
            api(repository, "pulls/" + str(pr["number"]), "PATCH", {"state": "open"})
            print("App setup PR reopened: " + pr["html_url"])
        else:
            print("App setup PR is current: " + pr["html_url"])
        return
    # Resolve formatting and all ownership checks before any remote writes.
    if api(repository, ref)["object"]["sha"] != base or branch_ref(repository) != head:
        raise ValueError("Setup base or branch changed; rerun App setup")
    parent = api(repository, "git/commits/" + base)
    entries = []
    for name, data in rendered.items():
        blob = api(
            repository,
            "git/blobs",
            "POST",
            {"encoding": "base64", "content": base64.b64encode(data).decode()},
        )
        entries.append(
            {"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    tree = api(
        repository,
        "git/trees",
        "POST",
        {"base_tree": parent["tree"]["sha"], "tree": entries},
    )
    old_tree = api(repository, "git/commits/" + head)["tree"]["sha"] if head else None
    if tree["sha"] != old_tree:
        parents = [head, base] if head and head != base else [base]
        commit = api(
            repository,
            "git/commits",
            "POST",
            {
                "message": TITLE,
                "tree": tree["sha"],
                "parents": parents,
                "author": identity,
                "committer": identity,
            },
        )
        if (
            api(repository, ref)["object"]["sha"] != base
            or branch_ref(repository) != head
        ):
            raise ValueError(
                "Setup base or branch changed before commit; rerun App setup"
            )
        if head:
            api(
                repository,
                "git/refs/heads/" + BRANCH,
                "PATCH",
                {"sha": commit["sha"], "force": False},
            )
        else:
            api(
                repository,
                "git/refs",
                "POST",
                {"ref": "refs/heads/" + BRANCH, "sha": commit["sha"]},
            )
    if pr and pr["state"] == "open":
        print("App setup PR updated: " + pr["html_url"])
    elif pr and head and not pr.get("merged_at"):
        api(repository, "pulls/" + str(pr["number"]), "PATCH", {"state": "open"})
        print("App setup PR reopened: " + pr["html_url"])
    else:
        created = api(
            repository,
            "pulls",
            "POST",
            {"title": TITLE, "body": BODY, "head": BRANCH, "base": default},
        )
        print("App setup PR opened: " + created["html_url"])
