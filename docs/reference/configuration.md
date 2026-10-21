# Consumer configuration reference

Each consumer has four primary inputs:

| Input            | Contract                                                                    |
| ---------------- | --------------------------------------------------------------------------- |
| `plugin.toml`    | Schema 1 identity, capabilities, paths, operations, and distribution policy |
| `version.txt`    | Authoritative plugin version; conflicting MSBuild versions are rejected     |
| `pyproject.toml` | Native Python tooling environment, dependency ranges and uv settings        |
| `uv.lock`        | uv-resolved toolkit source, version, wheel hash and transitive dependencies |

Native `.python-version`, `.node-version`, `global.json`, `package.json`, and dependency lockfiles remain authoritative for their tools. Generated MSBuild properties and frontend bindings live under the ignored `.build` directory.

Configuration is strictly typed: unknown fields and incorrect value types fail loading. Add `#:schema .build/jpt/configs/plugin.schema.json` to `plugin.toml` for editor validation. Python adapters use model attributes; use `ctx.config.data()` when a plain dictionary is required.

## Configuration sections

| Section        | Fields and behavior                                                                                                                                                                                     |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `plugin`       | Stable `id`, `guid`, host `assembly`, display `name`, repository, owner, description, overview, category                                                                                                |
| `host`         | Official immutable `image`, `abi`, `framework`, `platform`                                                                                                                                              |
| `images`       | Immutable `playwright` and `actionlint` images                                                                                                                                                          |
| `backend`      | Host `entry`, named `projects`, `project_assemblies`, test project paths, source roots; optional `unmanaged_projects` retain upstream MSBuild defaults                                                  |
| `architecture` | Exact allowed project `edges` and forbidden source tokens per directory                                                                                                                                 |
| `frontend`     | `enabled`; optional directory, custom build adapter (the shared builder is the default), bundle entries, admin entry/template, style/script markers, required assets, extra assets and boundary checker |
| `sandbox`      | Plugin settings-page identifier and asset-route prefix                                                                                                                                                  |
| `browser`      | Browser test directory and group inventory path                                                                                                                                                         |
| `source`       | Maintained `roots` and explicit additional `files`                                                                                                                                                      |
| `validation`   | Enabled groups, check targets, default unit groups, acceptance groups, path-classification rules and optional matrix job names                                                                          |
| `operations`   | Named replacements or after-default commands with optional working directory                                                                                                                            |
| `distribution` | Web ZIP capability, shipped notices, DLL license inventory, payload notices, licensed paths and web exclusions                                                                                          |

Paths are repository-relative and cannot escape the checkout. Entrypoint keys are relative to the frontend directory; asset and distribution output names must be canonical relative paths without `..` components. Duplicate asset outputs fail the build. Frontend entries are relative to the configured frontend directory; admin templates and source inventory paths are relative to the repository. Optional layers are omitted rather than generated empty.

An operation declares exactly one of `commands` (replace the operation) or `after` (extend a standard operation). Each is a nonempty list of nonempty argv arrays. `after` runs only after the standard operation succeeds; commands run in order and stop at the first failure. An `after` hook cannot introduce an unknown operation or bypass standard integration checks. Full replacements remain available for consumer-specific behavior.

```toml
[operations.check-tooling]
after = [["{python}", "-m", "mypy"]]
```

The optional repository-relative `cwd` applies only to declared commands. Supported placeholders are `{python}`, `{root}`, `{toolkit}`, `{pnpm}`, and `{dotnet}`. Arguments are passed directly without a shell. Operation names include `check-frontend`, `test-tooling`, `test-installation`, and the corresponding command/target names. Extra test arguments reach the standard operation or replacement commands, never the `after` commands. Standard tooling tests discover Python tests under `tests/tooling` when that directory exists; consumers without tooling tests do not need a synthetic framework test.

Unknown maintained inputs fail CI classification until a rule covers them. A toolkit update changes evidence fingerprints. The locked wheel version, immutable reusable-workflow revisions and their release-version comments, and the versioned Renovate preset must agree; partial updates fail validation.

### Fixture-only browser result reuse

An optional `validation.browser_inputs.<group>` table declares `files` (maintained test, fixture, and configuration patterns), `assets` (built `dist/web` patterns), `projects`, `workers`, and `runner` (the GitHub runner label). Every pattern must match at least one file. Use this only for tests whose runtime inputs are fully represented by those assets and fixtures. Suites using a live application server retain ordinary fingerprints.

Planning builds the frontend before looking for reusable results. Result identity covers emitted asset bytes, declared test inputs, browser image, command, project/worker settings, and runner image. Broad impact rules still select checks, but unrelated source changes need not invalidate a fixture-only result. Browser jobs verify planned assets and execution settings before testing. Missing or expired evidence executes checks, and explicit full validation bypasses reuse. Receipt schema changes invalidate earlier evidence.

## Commands

| Command                             | Result                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------- |
| `prepare`                           | Export wheel resources under `.build/jpt` without installing packages     |
| `bootstrap`                         | Restore Node and .NET dependencies and prepare verified host references   |
| `doctor`                            | Diagnose toolchain and configuration mismatches                           |
| `dev`                               | Run the checkout-owned disposable server and supervisor                   |
| `build frontend` / `build backend`  | Build one component                                                       |
| `build`                             | Capture source and build all declared distribution artifacts              |
| `check [target]`                    | Run maintained checks                                                     |
| `format [target]`                   | Apply maintained formatting                                               |
| `test [group]`                      | Run a group; defaults to configured unit groups                           |
| `validate --full`                   | Run configured checks, unit groups, packaging and acceptance groups       |
| `sync-bindings [--check / --apply]` | Preview, check or apply native lock and toolkit-reference synchronization |
| `sync-bindings --base-ref <ref>`    | Use an authoritative Git baseline for a partial update                    |
| `sync-bindings --recover`           | Restore an interrupted transaction while preserving newer edits           |
| `paths`                             | Print stable consumer tool and toolkit paths as JSON                      |
| `editor vscode [--apply]`           | Preview or apply VS Code settings while preserving unrelated settings     |
| `new`                               | Generate an independent uv consumer using the installed toolkit version   |

Prefix each command with `uv run jpt`. CI first runs `uv sync --locked`, then uses `uv run --no-sync jpt`.
Backend tests compile missing embedded frontend resources using already restored dependencies. Verified prebuilt browser assets can be supplied by CI without requiring a second frontend build.

The toolkit dependency uses a canonical GitHub release wheel URL. uv verifies its locked SHA256 during installation. Local candidate wheels are supported only for isolated validation. Published builds require released URLs; source bundles include the exact wheel and matching source distribution.

## Source and evidence boundaries

Maintained source inputs cannot be symlinks, including links to files inside the checkout. Every explicit `source.files` entry must exist. Optional source directories may be absent. Frontend sources, build adapters, templates, styles, assets, and extra assets must belong to the maintained source inventory. Add external input directories to `source.roots`, or individual inputs to `source.files`; their bytes participate in frontend evidence fingerprints.

Optional `validation.job_names` and `validation.aggregate_jobs` map enabled groups to nonempty, unique job-name lists. `validation.artifact_jobs` is also a nonempty, unique list. Empty lists cannot disable a validation gate. Release evidence must identify the latest authenticated run attempt and a successful aggregate job from that attempt.

## Shared presets and synchronization

Keep native configuration entrypoints in the consumer so editors and tools discover them normally: `eslint.config.mjs` passes its native ESLint dependency imports to `createConfig({ js, defineConfig, prettier, globals, tseslint })` from `.build/jpt/configs/eslint.mjs`; `[tool.ruff]` extends `.build/jpt/configs/ruff.toml`; `.prettierrc.mjs` imports `.build/jpt/configs/prettier.mjs`; frontend TypeScript configuration extends `../.build/jpt/configs/tsconfig.json`. Keep file-selection paths, language-version selections, and plugin-specific overrides in those native files. Native dependency imports remain visible to editors and dependency analyzers; the factory contains the shared rules. Consumer dependency manifests and lockfiles remain authoritative.

Synchronization preserves workflow triggers, permissions, schedules, extra jobs, Renovate policy and surrounding formatting. It updates the native toolkit dependency and lockfile, static toolkit `uses` references, their release-version comments and existing top-level toolkit presets. Ambiguous syntax, drift and unavailable releases stop without partial changes. Interrupted writes retain a recovery journal under `.build`; `--recover` refuses to discard newer edits.

Use `--base-ref REF` for an authoritative Git baseline or a partially updated PR. The trusted updater reads release metadata without importing proposed toolkit code and invokes uv with source builds disabled. Every target must be a published canonical wheel. Checks, CI and release builds reject mismatched wheel versions, workflow references and presets.

Bootstrap never edits editor settings. Optional editor setup accepts JSON settings; existing JSON-with-comments settings remain intact, and `paths` provides values for manual setup.
