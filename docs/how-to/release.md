# Prepare releases

Toolkit releases and plugin releases are independent. Public plugin publication and production installation are manual rollout steps.

## Toolkit

Install the release GitHub App on the toolkit repository with Contents, Pull requests and Issues read/write access. Set Actions variable `PLUGIN_APP_CLIENT_ID` and secret `PLUGIN_APP_PRIVATE_KEY`. The toolkit release workflow requires App credentials so its release PRs trigger ordinary validation; it does not fall back to `GITHUB_TOKEN` for PR creation. Require the toolkit and both starter validation jobs before merging release PRs.

Use Conventional Commits or conventional squash titles. Below 1.0, fixes and features bump the patch version and breaking changes bump the minor version. Release-please maintains `.release-please-manifest.json` from completed releases; preserve it when configuring subsequent releases.

1. Review and merge the release-please PR containing `version.txt`, the manifest and `CHANGELOG.md` changes. **Prepare toolkit release** creates a `v`-prefixed tag and an unpublished draft.
2. The workflow builds `dist/jellyfin-plugin-toolkit-<version>.tar.gz` and `dist/SHA256SUMS` from a clean checkout at the tagged revision. Reusable toolkit CI validates both generated consumer variants against those exact bytes, including installation and reproducibility, before attaching them to the draft. Failed validation leaves the draft unpublished without new attachments.
3. Review the archive inventory, notices, embedded source revision and checksums. Validate independent consumer fixtures against the same candidate archive using `JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE`, with matching lock checksums, workflow SHAs/comments and preset versions.
4. Manually publish the draft only after the workflow and the independent consumer variants pass. Verify an unauthenticated consumer download before merging adoption PRs. Development checkouts download the locked release into an ignored cache; source distributions bundle the verified archive for offline rebuilding. Published tags and assets are immutable.

For upload failures, use **Re-run failed jobs** to reuse the retained candidate artifact. Candidates are retained for 14 days. Alternatively, manually run **Prepare toolkit release** on `main` with its existing draft `tag`; this validates the tagged source again. Identical existing assets are retained, missing assets are uploaded, and conflicting assets or published releases are rejected. Never overwrite an asset or move a release tag to repair a tagged source change; prepare a new version.

Consumers extend the shared `default.json` preset as `github>rakuzen25/jellyfin-plugin-toolkit#v0.1.0`. Renovate groups subsequent archive version/checksum, reusable workflow SHAs/version comments, and preset tag changes into a manually reviewed PR. Tags and presets can be visible before draft attachments are public; partial updates fail pin validation and must not be merged. The preset uses the native release-attachment checksum datasource and GitHub Actions/config-preset managers without arbitrary post-upgrade scripts.

## Plugins

Consumers keep event triggers and explicit job permissions in their own small workflows. Shared workflow references use immutable commit SHAs matching the archive's recorded revision.

Release preparation uses release-please to create version changes and unpublished drafts. A repository-scoped App can be configured through `PLUGIN_APP_CLIENT_ID` and `PLUGIN_APP_PRIVATE_KEY`; manual release attachment can use the workflow token. Configure the catalog notification App only for the chosen catalog repository.

### Maintain internal project update rules

After adding or removing backend projects, lockfiles, or internal assemblies, regenerate the Release Please rules from your consumer checkout:

```bash
./plugin release config
./plugin release config --apply
./plugin release config --check
# Validated Release Please internal project update rules
```

The first command previews a diff. `--apply` writes only `release-please-config.json`; `--check` exits with status 1 when rules are incomplete or stale. These commands use the maintained source inventory, `backend.project_assemblies`, and `plugin.assembly` from `plugin.toml`. Keep those assembly names complete. Generated starters already contain the required rules.

The toolkit manages JSON selectors for internal dependency ranges in NuGet `Project` records under `packages["."].extra-files`. It preserves unrelated settings and extra-file entries, supports a single `.` package with release type `simple`, and does not change external package versions. Ordinary validation checks these rules before selecting jobs; release preparation checks them before invoking Release Please.

If a custom rule overlaps a maintained NuGet lockfile, the command names the file and asks you to remove that extra-file entry. Remove the conflicting entry explicitly, then preview and apply the generated rules. The toolkit does not translate old selectors or evaluate arbitrary custom selectors for equivalence.

### Repair an incomplete release PR

Use this procedure when a release PR bumps `version.txt` but leaves internal project ranges at the previous version:

1. Publish and adopt a toolkit release containing the rule generator, following the toolkit release and [foundation update](develop.md#adopt-a-foundation-update) procedures.
2. Remove overlapping hand-written NuGet selectors, run the configuration commands above on the plugin's main branch, and merge the reviewed configuration change.
3. Let Release Please refresh the release PR, then check out its updated branch.
4. Generate the existing project identity and use the pinned SDK to regenerate maintained backend lockfiles:

    ```bash
    PYTHONPATH="$PWD/.build/toolkit/current" python3 - <<'PY'
    import subprocess
    from jellyfin_plugin_toolkit.build.backend import maintained_csharp
    from jellyfin_plugin_toolkit.context import Context

    ctx = Context.load()
    ctx.generated()
    for project in maintained_csharp(ctx, "*.csproj"):
        subprocess.run(
            ["dotnet", "restore", str(project), "--force-evaluate",
             "-p:RestoreLockedMode=false", "-p:NuGetAudit=false", "-m:1"],
            cwd=ctx.root, check=True,
        )
    PY
    ./plugin release check
    ./plugin release config --check
    ```

    Keep the existing pinned Jellyfin references available. Restore uses the committed lockfiles as its dependency baseline; review the diff and reject external dependency version changes.

5. Commit the reviewed lockfiles and require complete release-candidate validation before merging. Publish the resulting draft manually after artifact review.

`Project-version metadata is stale` identifies the lockfile, framework, project, dependency, actual range, and expected range. Fix the release configuration and regenerate lockfiles; rerunning unchanged CI cannot repair the metadata.

### Review release artifacts

The release workflow validates the exact tagged source, runs fresh package installation, reproducibility and advisory checks, then attaches the plugin ZIP, matching source ZIP, manifest, checksums, authenticated receipt and build provenance. A web ZIP is required only when the plugin declares standalone web distribution. Already validated bytes are retained on upload retries.

Before manually publishing a plugin draft, verify the catalog accepts its configured signer, toolkit revision, identity, required checks and source inventory. Preserve configuration and state for rollback; installation into a production server is a separate explicit operation.

## Optional Renovate binding completion

Consumers may opt into `.github/workflows/sync-bindings.yml` through an immutable reusable-workflow reference matching their toolkit lock. Generate a new repository with `--sync-workflow` to include its caller, or use `jellyfin_plugin_toolkit.workflows.render_sync` from the selected toolkit to create the optional caller in an existing consumer. The consumer owns its validation workflow name, triggers, bot login, and permissions.

The caller uses `workflow_run` after validation completes (including failed validation), plus manual dispatch with a Renovate PR number. Configure `PLUGIN_APP_CLIENT_ID` and `PLUGIN_APP_PRIVATE_KEY` for the existing repository App with Contents and Workflows write permissions and Pull requests read permission. Ordinary validation remains read-only. The updater accepts only the configured bot's open, same-repository toolkit PRs. It materializes binding files as data, runs the verified base release's updater, and commits no partial update on conflicts or unsupported binding contracts.

The shared Renovate preset includes `jellyfin-plugin-toolkit-sync@users.noreply.github.com` in `gitIgnoredAuthors`. Preserve that entry if overriding this array so Renovate can regenerate its branch and the companion can reapply synchronization. Human edits are not ignored. Toolkit adoption remains manually reviewed; synchronization never merges or publishes. Missing App permissions or unsupported upgrades require maintainer action, not a fallback write token.
