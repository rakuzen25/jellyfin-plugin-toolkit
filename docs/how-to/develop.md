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
uv run jpt validate
```

Pass runner arguments after an explicit target and separator, for example `uv run jpt test browser -- --project workflow-chromium`. The first separator is consumed; the remaining arguments reach the runner unchanged.

Use `uv run jpt --debug COMMAND` for failure tracebacks. Redirected output and CI help use plain formatting.

uv synchronizes Python dependencies before local commands. Bootstrap prepares Node, .NET and Jellyfin dependencies. Full validation builds the complete distribution and executes the consumer's enabled acceptance operations, including installation and reproducibility.

The toolkit source checkout uses the shared `jpt check` implementation through `pnpm check`, including Ruff defaults, strict mypy and basedpyright, JavaScript and TypeScript checks, documentation links, and automation validation. Run `uv run python -m mypy` or `uv run python -m basedpyright` for an individual type checker. `pnpm format` applies the shared formatting checks without running type checkers.

## Generate a repository

From a toolkit source checkout:

```sh
uv run jpt new /tmp/example-plugin --id example-plugin --name 'Example Plugin' --repository example/example-plugin --admin
```

Replace `example/example-plugin` with your destination repository. Omit `--admin` for a backend-only plugin. Use `--host-project server/Entry.csproj` for a different backend path. The destination must not exist. Keep its generated GUID stable after publication.

Generation needs no App identity or credentials. After pushing the repository and configuring `PLUGIN_APP_CLIENT_ID` and `PLUGIN_APP_PRIVATE_KEY` in Actions settings, dispatch **Set up connected GitHub App**. Review and merge its configuration PR. Pushes to `main` also check setup when `renovate.json`, `.github/jpt-automation.json`, or its workflow caller changes. After changing App credentials, dispatch the workflow manually.

Generation uses `uv init` and `uv add` to create an independent project and lock the canonical released toolkit wheel. It works outside a consumer checkout. For an unpublished candidate, build the distribution pair first:

```sh
uv build
uv run jpt new /tmp/candidate-plugin --id candidate-plugin --name Candidate --repository example/candidate-plugin --toolkit-wheel dist/jellyfin_plugin_toolkit-0.2.0-py3-none-any.whl --toolkit-revision "$(git rev-parse HEAD)"
```

Replace the wheel filename with the version in `pyproject.toml`. Keep its matching source distribution beside it. Local candidate projects retain the wheel under `.build/toolkit-dist/` with its native filename, so the locked path remains valid in source bundles; generation prepares this layout automatically. Candidate dependencies remain local wheels inside the generated checkout; publish and adopt the canonical release URL before merging production adoption changes.

Toolkit wheels, matching source archives, and published release checksums are retained in `.build/toolkit-dist/` and included at that path in plugin source bundles. Resource preparation replaces only `.build/jpt/` and preserves these distributions. Deleting `.build/` removes them: published distributions can be downloaded again, while unpublished candidate artifacts must be restored or rebuilt.

Existing candidate projects must be regenerated or manually updated: move the wheel and matching source archive from `build-support/` into `.build/toolkit-dist/`, then run `uv add ./.build/toolkit-dist/<wheel-filename>` to update the dependency and lockfile. The old candidate path is unsupported. Published URL dependencies need no dependency or lockfile changes.

## Adopt a foundation update

Renovate proposes only the published toolkit wheel URL. Toolkit releases have no Renovate release-age delay because publication is maintained here; third-party dependencies retain their configured cutoff. Workflow references and the Renovate preset do not select versions independently. Enable the optional toolkit synchronization workflow for App-authored companion commits, or complete the lockfile and references locally using the trusted base toolkit:

```sh
uv run jpt toolkit sync --base-ref main
uv run jpt toolkit sync --base-ref main --apply
uv run jpt bootstrap
uv run jpt toolkit sync --check
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

`uv run jpt test advisories` reports dependency findings as warnings by default, including findings with published fixes, so unrelated advisories do not block dependency updates or automerge. Set `[advisories] fail_on_findings = true` to block validation on unwaived findings with published fixes, regardless of severity or current dependency constraints. Findings explicitly lacking a published fix remain warnings. Incomplete metadata and audit service/configuration failures fail validation. The fresh report is saved to `dist/evidence/advisories.json`; CI runs it on every validation and daily safety check. See [advisory waivers](../reference/configuration.md#advisory-waivers) for reviewed, temporary exceptions.

The versioned `tooling` Renovate preset supplies the shared maintenance policy. Generated plugins extend it alongside the plugin preset; toolkit synchronization advances both tags with the wheel and workflow references. Standalone tools such as the catalog may extend only `github>rakuzen25/jellyfin-plugin-toolkit:tooling#VERSION` and review preset upgrades independently. Routine third-party updates automerge after checks and the one-day delay; major and runtime/compiler/package-manager minor upgrades require review. Repository-specific exceptions remain local.
