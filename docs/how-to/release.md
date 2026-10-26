# Prepare releases

Toolkit releases and plugin releases are independent. Public plugin publication and production installation are manual rollout steps.

## Toolkit

Install the release GitHub App on the toolkit repository with Contents, Pull requests and Issues read/write access, plus Workflows write access where toolkit synchronization is enabled. Set Actions variable `PLUGIN_APP_CLIENT_ID` and secret `PLUGIN_APP_PRIVATE_KEY`. The toolkit release workflow requires App credentials so its release PRs trigger ordinary validation; it does not fall back to `GITHUB_TOKEN` for PR creation. Require the toolkit and both starter validation jobs before merging release PRs.

Require the shared PR-title check and configure squash commit subjects to use the PR title. The check accepts Conventional Commit titles and runs independently of code validation, so title or body edits do not repeat builds and tests. Base-branch retargeting reruns code validation. Below 1.0, fixes and features bump the patch version and breaking changes bump the minor version. Release-please maintains `.release-please-manifest.json` from completed releases; preserve it when configuring subsequent releases.

1. Review and merge the release-please PR containing `pyproject.toml`, the atomically updated `uv.lock`, the manifest and `CHANGELOG.md` changes. **Prepare toolkit release** creates a `v`-prefixed tag and an unpublished draft.
2. Regular toolkit CI uses `uv build` to build the standard wheel, matching source distribution, `SHA256SUMS` and build provenance from a clean checkout at the tagged revision. It validates both generated consumer variants against those exact bytes, including installation and reproducibility. Release preparation waits for that run, authenticates its successful jobs and validation receipt, and attaches its exact candidate artifact. Failed validation leaves the draft unpublished without new attachments.
3. Review wheel and source contents, notices, source revision, checksums and attestations. Validate independent consumer copies against those exact candidate wheels through native local-wheel dependencies, with matching workflow SHAs/comments and preset versions.
4. Manually publish the draft only after the workflow and the independent consumer variants pass. Verify an unauthenticated consumer download before merging adoption PRs. After publication, generate both consumers’ final URL dependencies and lockfiles through `uv add`, then require green validation. Source bundles retain the exact wheel and source distribution; empty-cache reconstruction may require network access. Published tags and assets are immutable.

For upload failures, use **Re-run failed jobs** to reuse the retained candidate artifact. Candidates are retained for 14 days. Alternatively, manually run **Prepare toolkit release** on `main` with its existing draft `tag`; this reuses successful regular CI when its artifacts remain available. If eligible artifacts are absent or expired, the retry validates the immutable tagged source once before attachment. Failed validation must be fixed or rerun before retrying release preparation. Identical existing assets are retained, missing assets are uploaded, and conflicting assets or published releases are rejected. Never overwrite an asset or move a release tag to repair a tagged source change; prepare a new version.

The native App-token action is maintained in the toolkit repository. When changing it, merge the action bootstrap first, then pin shared workflows to that reviewed main-branch commit before preparing the consumer release; both changes can ship in one release. Consumer workflow and action references use the immutable revision matching their published toolkit wheel.

Consumers extend the shared `default.json` preset as `github>rakuzen25/jellyfin-plugin-toolkit#v0.1.0`. Renovate groups subsequent wheel URL, reusable workflow SHAs/version comments and preset tag changes into a manually reviewed PR. Tags and presets can be visible before draft attachments are public; partial updates fail pin validation and must not be merged. The preset uses GitHub release and Actions/config-preset managers. Trusted synchronization invokes uv to complete the dependency lockfile.

## Plugins

Consumers keep event triggers and explicit job permissions in their own small workflows. Shared workflow references use immutable commit SHAs matching the wheel release's source revision.

Release preparation uses release-please to create version changes and unpublished drafts. The existing repository App is required through `PLUGIN_APP_CLIENT_ID` and `PLUGIN_APP_PRIVATE_KEY` for all repository writes, including manual release attachment. `GITHUB_TOKEN` is used for reads, CI artifacts, and attestations. Configure the catalog notification App only for the chosen catalog repository.

### Maintain internal project update rules

After adding or removing backend projects, lockfiles, or internal assemblies, regenerate the Release Please rules from your consumer checkout:

```bash
uv run jpt release config
uv run jpt release config --apply
uv run jpt release config --check
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
    uv run python - <<'PY'
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
    uv run jpt release check
    uv run jpt release config --check
    ```

    Keep the existing pinned Jellyfin references available. Restore uses the committed lockfiles as its dependency baseline; review the diff and reject external dependency version changes.

5. Commit the reviewed lockfiles and require complete release-candidate validation before merging. Publish the resulting draft manually after artifact review.

`Project-version metadata is stale` identifies the lockfile, framework, project, dependency, actual range, and expected range. Fix the release configuration and regenerate lockfiles; rerunning unchanged CI cannot repair the metadata.

### Review release artifacts

The release workflow validates the exact tagged source, runs fresh package installation, reproducibility and advisory checks, then attaches the plugin ZIP, matching source ZIP, manifest, checksums, authenticated receipt and build provenance. A web ZIP is required only when the plugin declares standalone web distribution. Already validated bytes are retained on upload retries.

Before manually publishing a plugin draft, verify the catalog accepts its configured signer, toolkit revision, identity, required checks and source inventory. Preserve configuration and state for rollback; installation into a production server is a separate explicit operation.

## Optional Renovate binding completion

Consumers may opt into `.github/workflows/sync-bindings.yml` through an immutable reusable-workflow reference matching their locked toolkit release. Generate a new repository with `--sync-workflow` to include its caller, or use `jellyfin_plugin_toolkit.workflows.render_sync` from the selected toolkit to create the optional caller in an existing consumer. The consumer owns its validation workflow name, triggers, bot login, and permissions.

The published wheel URL is the sole Renovate update source. The shared preset disables independent toolkit workflow and preset updates while leaving other dependencies unchanged. A grouped PR alone does not enforce equal versions. The updater derives all related references from the selected wheel release and rejects drafts or releases missing the wheel/source pair before running uv.

The caller uses `workflow_run` after validation completes (including failed validation), plus manual dispatch with a Renovate PR number. Shared read-only metadata routing skips unrelated, closed, fork and superseded PRs before checkout or environment installation. Selected toolkit updates check out the exact base revision; trusted code recomputes every proposed byte before a bounded write. A changing base or head stops the write and requires a fresh synchronization run. Configure `PLUGIN_APP_CLIENT_ID` and `PLUGIN_APP_PRIVATE_KEY` for the existing repository App with Contents and Workflows write permissions and Pull requests read permission. Ordinary validation remains read-only. The updater accepts only the configured bot's open, same-repository toolkit PRs. It materializes binding files as data, runs the trusted base release's updater, and commits no partial update on conflicts or unsupported binding contracts.

The **Set up connected GitHub App** workflow runs only on manual dispatch from the default branch. Dispatch it when first connecting an App or changing its identity. Rotating credentials for the same App only requires retrying the affected operation. It authenticates before restoring project tooling, then discovers the slug and bot account ID and opens or updates one reviewed PR from `jpt/automation-setup`. Starter generation requires no identity input. The PR changes only `renovate.json` and `.github/jpt-automation.json`; the latter records the managed App slug and bot ID. On App changes, setup replaces only the previously managed email in `gitIgnoredAuthors`, preserving unrelated entries and configuration. Merging this PR does not run setup again.

Merge the setup PR to enable toolkit synchronization. Builds, ordinary PR checks, releases, uploads and catalog notifications continue while setup is pending. Manual dispatch can reopen a closed, unmerged setup PR. Human edits or unrelated changes on the setup branch stop setup updates and require review.

Write jobs resolve the bot from the token action’s App slug and use its canonical name and email for both author and committer. Human edits are not ignored. Toolkit adoption remains manually reviewed; setup and synchronization never merge or publish. Missing App permissions or unsupported upgrades require maintainer action, not a fallback write token.

### Troubleshoot the connected App

All App-backed workflows use the toolkit's scoped token action. Each operation requests its own explicit repository and permission scopes; authentication failure retains GitHub's original error and adds recovery guidance in the job summary. Tokens are revoked at job completion.

| Failure                                              | Recovery                                                                                                                             |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Missing ownership record or changed App identity     | Dispatch **Set up connected GitHub App** on the default branch, then review and merge its configuration PR.                          |
| Malformed ownership record or Renovate configuration | Repair or restore the tracked configuration before running setup.                                                                    |
| Managed App identity absent from Renovate            | Run setup and merge its PR, or restore the managed entry.                                                                            |
| Missing or invalid credentials                       | Correct Actions variable `PLUGIN_APP_CLIENT_ID` or secret `PLUGIN_APP_PRIVATE_KEY`, then retry the affected operation.               |
| Installation or requested-permission failure         | Check the original GitHub error, install the App on the selected repositories, and approve the required permissions before retrying. |

App setup does not repair credentials, installations, or permissions. Ordinary CI validates tracked configuration offline and requires no App secrets. Missing initial setup is a notice; operations requiring Renovate recognition fail with a setup-workflow link. Releases and catalog operations do not require this ownership record. Downstream validation, branch-protection, rate-limit, and network failures retain their own diagnostics.

When adding native shared actions, merge their implementation and tests as a bootstrap change first. Pin reusable workflows to that reviewed commit in a follow-up change and release both through the normal verified process. Consumers adopt the published wheel and matching immutable action/workflow references together. Upgrade tracked configuration and callers directly; retain no forwarding actions, legacy configuration readers or alternate execution paths. Published releases remain immutable.
