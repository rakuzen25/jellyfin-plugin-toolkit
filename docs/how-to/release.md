# Prepare releases

Toolkit releases and plugin releases are independent. Public plugin publication and production installation are manual rollout steps.

## Toolkit

Install the release GitHub App on the toolkit repository with Contents, Pull requests and Issues read/write access. Set Actions variable `PLUGIN_APP_CLIENT_ID` and secret `PLUGIN_APP_PRIVATE_KEY`. The toolkit release workflow requires App credentials so its release PRs trigger ordinary validation; it does not fall back to `GITHUB_TOKEN` for PR creation. Require the toolkit and both starter validation jobs before merging release PRs.

Use Conventional Commits or conventional squash titles. Below 1.0, fixes and features bump the patch version and breaking changes bump the minor version, matching the pilot plugins. The initial version is `0.1.0`; an empty release manifest lets release-please propose that first release. Do not seed the manifest with `0.1.0` as though it were already released or keep a permanent `release-as` override.

1. Review and merge the release-please PR containing `version.txt`, the manifest and `CHANGELOG.md` changes. **Prepare toolkit release** creates a `v`-prefixed tag and an unpublished draft.
2. The workflow builds `dist/jellyfin-plugin-toolkit-<version>.tar.gz` and `dist/SHA256SUMS` from a clean checkout at the tagged revision. Reusable toolkit CI validates both generated consumer variants against those exact bytes, including installation and reproducibility, before attaching them to the draft. Failed validation leaves the draft unpublished without new attachments.
3. Review the archive inventory, notices, embedded source revision and checksums. Validate both LyricBridge and Music Metadata Manager against the same candidate archive using `JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE`, with matching lock checksums, workflow SHAs/comments and preset versions.
4. Manually publish the draft only after the workflow and both pilots pass. Verify an unauthenticated consumer download before merging adoption PRs or removing temporarily source-bundled candidate archives. Published tags and assets are immutable.

For upload failures, use **Re-run failed jobs** to reuse the retained candidate artifact. Candidates are retained for 14 days. Alternatively, manually run **Prepare toolkit release** on `main` with its existing draft `tag`; this validates the tagged source again. Identical existing assets are retained, missing assets are uploaded, and conflicting assets or published releases are rejected. Never overwrite an asset or move a release tag to repair a tagged source change; prepare a new version.

Consumers extend the shared `default.json` preset as `github>rakuzen25/jellyfin-plugin-toolkit#v0.1.0`. Renovate groups subsequent archive version/checksum, reusable workflow SHAs/version comments, and preset tag changes into a manually reviewed PR. Tags and presets can be visible before draft attachments are public; partial updates fail pin validation and must not be merged. The preset uses the native release-attachment checksum datasource and GitHub Actions/config-preset managers without arbitrary post-upgrade scripts.

When resetting an unpublished toolkit `v1.0.0` draft to `v0.1.0`, remove that draft and only its associated unpublished tag before enabling release automation. Both existing pilots need a one-time coordinated repin to the new version; do not depend on Renovate to perform a version downgrade. Keep their own plugin versions unchanged. Normal Renovate updates resume after this repin.

## Plugins

Consumers keep event triggers and explicit job permissions in their own small workflows. Shared workflow references use immutable commit SHAs matching the archive's recorded revision.

Release preparation uses release-please to create version changes and unpublished drafts. A repository-scoped App can be configured through `PLUGIN_APP_CLIENT_ID` and `PLUGIN_APP_PRIVATE_KEY`; manual release attachment can use the workflow token. Configure the catalog notification App only for the chosen catalog repository.

The release workflow validates the exact tagged source, runs fresh package installation, reproducibility and advisory checks, then attaches the plugin ZIP, matching source ZIP, manifest, checksums, authenticated receipt and build provenance. A web ZIP is required only when the plugin declares standalone web distribution. Already validated bytes are retained on upload retries.

Before manually publishing a plugin draft, verify the catalog accepts its configured signer, toolkit revision, identity, required checks and source inventory. Preserve configuration and state for rollback; installation into a production server is a separate explicit operation.
