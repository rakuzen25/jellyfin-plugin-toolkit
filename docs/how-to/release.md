# Prepare releases

Toolkit releases and plugin releases are independent. Public plugin publication and production installation are manual rollout steps.

## Toolkit

1. Run `./toolkit test` with the pinned Python version. Validate both pilot migrations and generated backend-only and administrator-page consumers against the same candidate archive, including installation and reproducibility.
2. Commit and review the toolkit implementation. Build from its clean committed checkout with `./toolkit pack --release`. This refuses dirty or untracked source.
3. Create a draft tagged `v` plus `version.txt` and attach `dist/jellyfin-plugin-toolkit-<version>.tar.gz` and `dist/SHA256SUMS`, replacing `<version>` with the exact version. Review the archive inventory, notices, source revision and checksums.
4. Manually publish the reviewed initial toolkit release after both pilots pass. Verify a clean consumer download before removing any temporarily source-bundled candidate archive.
5. Exercise Renovate against consecutive releases. Its grouped PR must update the archive version/checksum, all reusable workflow SHAs, and the pinned shared preset tag together. Merge manually after validation; major versions require explicit review.

The shared Renovate preset is `default.json`, consumed as `github>rakuzen25/jellyfin-plugin-toolkit#v1.0.0`. It uses the native release-attachment checksum datasource and GitHub Actions/config-preset managers, without arbitrary post-upgrade scripts.

## Plugins

Consumers keep event triggers and explicit job permissions in their own small workflows. Shared workflow references use immutable commit SHAs matching the archive's recorded revision.

Release preparation uses release-please to create version changes and unpublished drafts. A repository-scoped App can be configured through `PLUGIN_APP_CLIENT_ID` and `PLUGIN_APP_PRIVATE_KEY`; manual release attachment can use the workflow token. Configure the catalog notification App only for the chosen catalog repository.

The release workflow validates the exact tagged source, runs fresh package installation, reproducibility and advisory checks, then attaches the plugin ZIP, matching source ZIP, manifest, checksums, authenticated receipt and build provenance. A web ZIP is required only when the plugin declares standalone web distribution. Already validated bytes are retained on upload retries.

Before manually publishing a plugin draft, verify the catalog accepts its configured signer, toolkit revision, identity, required checks and source inventory. Preserve configuration and state for rollback; installation into a production server is a separate explicit operation.
