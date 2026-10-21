# Develop a plugin

Install uv at the version required by `pyproject.toml`, plus Node, pnpm and the .NET SDK declared by the plugin's native pin files. Docker must be available to your user. The supported host is Linux x64; uv downloads and selects the pinned Python 3.15.0.

From an existing consumer checkout:

```sh
uv sync --locked
uv run jpt bootstrap
uv run jpt doctor
uv run jpt dev --port 18096
```

Bootstrap restores frozen pnpm and NuGet dependencies and extracts references from the pinned official Jellyfin image. uv manages the Python environment and enforces the project's 24-hour dependency release-age cutoff. The development server owns a disposable configuration directory and binds to loopback. Optional `--media-mount /absolute/media/path:/media` mounts are read-only.

Use the supervisor's rebuild and pause controls during development. Backend replacements compile before the working server stops; failed replacements restore the previous plugin. Successful builds trigger ordinary page reloads, and pending changes survive pause/resume.

Run component checks before a complete acceptance run:

```sh
uv run jpt check
uv run jpt test
uv run jpt validate --full
```

uv synchronizes Python dependencies before local commands. Bootstrap prepares Node, .NET and Jellyfin dependencies. Full validation builds the complete distribution and executes the consumer's enabled acceptance operations, including installation and reproducibility.

## Generate a repository

From a toolkit source checkout:

```sh
uv run jpt new /tmp/example-plugin --id example-plugin --name 'Example Plugin' --repository example/example-plugin --admin
```

Replace `example/example-plugin` with your destination repository. Omit `--admin` for a backend-only plugin. Use `--host-project server/Entry.csproj` for a different backend path. The destination must not exist. Keep its generated GUID stable after publication.

Generation uses `uv init` and `uv add` to create an independent project and lock the canonical released toolkit wheel. It works outside a consumer checkout. For an unpublished candidate, build the distribution pair first:

```sh
uv build
uv run jpt new /tmp/candidate-plugin --id candidate-plugin --name Candidate --repository example/candidate-plugin --toolkit-wheel dist/jellyfin_plugin_toolkit-0.2.0-py3-none-any.whl --toolkit-revision "$(git rev-parse HEAD)"
```

Replace the wheel filename with the version in `pyproject.toml`. Keep its matching source distribution beside it. Local candidate projects retain the wheel under `build-support` with its native filename, so the locked path remains valid in source bundles; generation prepares this layout automatically. Candidate dependencies remain local wheels inside the generated checkout; publish and adopt the canonical release URL before merging production adoption changes.

## Adopt a foundation update

Review the grouped Renovate PR, then complete its native lockfile and immutable workflow references using the trusted base toolkit:

```sh
uv run jpt sync-bindings --base-ref main
uv run jpt sync-bindings --base-ref main --apply
uv run jpt bootstrap
uv run jpt sync-bindings --check
uv run jpt check
uv run jpt test
```

Set `GH_TOKEN` to a repository read token for local binding synchronization; GitHub workflows supply scoped tokens.

For a manual update, use `uv add 'jellyfin-plugin-toolkit @ RELEASE_WHEEL_URL'`, replacing `RELEASE_WHEEL_URL` with the versioned GitHub wheel URL. Synchronization invokes uv to update the lockfile and coordinates workflow SHAs and the Renovate preset. Publish and verify the toolkit release before merging consumer adoption PRs.

For toolkit development alongside a consumer, use uv's native editable override:

```sh
uv run --with-editable ../jellyfin-plugin-toolkit jpt check
```

The checkout's committed released-wheel dependency stays authoritative for packaging. Integration and reproducibility tests use candidate wheels in isolated consumer copies so they exercise distributable bytes.

Editors read consumer-native configurations, which import resources exported by `jpt prepare` under `.build/jpt`. Toolkit operations prepare these resources automatically. Inspect paths or opt into VS Code setup with:

```sh
uv run jpt paths
uv run jpt editor vscode
uv run jpt editor vscode --apply
```
