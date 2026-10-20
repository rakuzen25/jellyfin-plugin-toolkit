# Consumer configuration reference

Each consumer has three primary inputs:

| Input               | Contract                                                                    |
| ------------------- | --------------------------------------------------------------------------- |
| `plugin.toml`       | Schema 1 identity, capabilities, paths, operations, and distribution policy |
| `version.txt`       | Authoritative plugin version; conflicting MSBuild versions are rejected     |
| `toolkit.lock.json` | Schema 1 repository, `v`-prefixed release version, and archive SHA-256      |

Native `.python-version`, `.node-version`, `global.json`, `package.json`, and dependency lockfiles remain authoritative for their tools. Generated MSBuild properties and frontend bindings live under the ignored `.build` directory.

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

Unknown maintained inputs fail CI classification until a rule covers them. A toolkit update changes evidence fingerprints. Archive metadata, immutable reusable-workflow revisions and their release-version comments, and the versioned Renovate preset must agree; partial updates fail validation.

## Commands

| Command                            | Result                                                                           |
| ---------------------------------- | -------------------------------------------------------------------------------- |
| `bootstrap`                        | Verify tools/archive, restore dependencies, prepare verified host references     |
| `doctor`                           | Diagnose toolchain and configuration mismatches                                  |
| `dev`                              | Run the checkout-owned disposable server and supervisor                          |
| `build frontend` / `build backend` | Build one component                                                              |
| `build`                            | Capture source and build all declared distribution artifacts                     |
| `check [target]`                   | Run maintained checks without dependency installation                            |
| `format [target]`                  | Apply maintained formatting                                                      |
| `test [group]`                     | Run a group; defaults to configured unit groups                                  |
| `validate --full`                  | Run configured checks, unit groups, complete packaging and acceptance groups     |
| `toolkit sync [--check / --apply]` | Preview, check, or apply launcher and toolkit-reference synchronization          |
| `toolkit sync --base-ref <ref>`    | Upgrade using the verified base release; repair partial pin updates              |
| `toolkit sync --apply --repair`    | Explicitly restore the launcher at the current locked release                    |
| `toolkit sync --recover`           | Restore bindings after an interrupted transaction; preserve newer edits          |
| `toolkit paths`                    | Print stable consumer tool and toolkit paths as JSON                             |
| `toolkit editor vscode [--apply]`  | Preview or explicitly apply VS Code settings while preserving unrelated settings |
| `new`                              | Generate a consumer using the installed toolkit lock                             |

Backend tests compile missing embedded frontend resources using already restored dependencies. Verified prebuilt browser assets can be supplied by CI without requiring a second frontend build.

The launcher accepts verified archives from the source bundle, cache, or explicit `JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE` path. Only bootstrap installs a missing toolkit automatically; other commands diagnose a missing installation. Corrupt, incomplete, linked, or escaping archives are rejected before replacing the active installation.

## Source and evidence boundaries

Maintained source inputs cannot be symlinks, including links to files inside the checkout. Every explicit `source.files` entry must exist. Optional source directories may be absent. Frontend sources, build adapters, templates, styles, assets, and extra assets must belong to the maintained source inventory. Add external input directories to `source.roots`, or individual inputs to `source.files`; their bytes participate in frontend evidence fingerprints.

Optional `validation.job_names` and `validation.aggregate_jobs` map enabled groups to nonempty, unique job-name lists. `validation.artifact_jobs` is also a nonempty, unique list. Empty lists cannot disable a validation gate. Release evidence must identify the latest authenticated run attempt and a successful aggregate job from that attempt.

## Shared presets and synchronization

Keep native configuration entrypoints in the consumer so editors and tools discover them normally: `eslint.config.mjs` passes its native ESLint dependency imports to `createConfig({ js, defineConfig, prettier, globals, tseslint })` from `.build/toolkit/current/configs/eslint.mjs`; `[tool.ruff]` extends `.build/toolkit/current/configs/ruff.toml`; `.prettierrc.mjs` imports `.build/toolkit/current/configs/prettier.mjs`; frontend TypeScript configuration extends `../.build/toolkit/current/configs/tsconfig.json`. Keep file-selection paths, language-version selections, and plugin-specific overrides in those native files. Native dependency imports remain visible to editors and dependency analyzers; the factory contains the shared rules. Consumer dependency manifests and lockfiles remain authoritative.

Synchronization preserves workflow triggers, permissions, schedules, extra jobs, Renovate policy, and surrounding formatting. It only updates static toolkit `uses` references, their release-version comments, existing top-level toolkit presets, and canonical launcher bytes/permissions. Unknown binding contracts, existing base drift, ambiguous syntax, or unsupported upgrades stop without partial changes. Every release must include the binding descriptor and shared integration files. Skipped releases are supported when the base updater understands the proposed binding contract.

Without `--base-ref`, synchronization uses the last verified active release as its base, even if the working-tree lock has changed. A fresh checkout without an active installation must supply `--base-ref REF`. Use `--base-ref` for an authoritative Git baseline or a partially updated PR; the launcher selects and verifies that base release before importing the updater. The base and target must belong to the same toolkit repository. Keep their exact archives in `.build/toolkit/<sha256>.tar.gz`, or use `--download` to fetch missing archives. The proposed release is never imported by synchronization. Same-release launcher edits require the explicit `--apply --repair` command after reviewing the preview. Interrupted writes retain a recovery journal under `.build`; `--recover` rolls them back and refuses to discard newer edits.

Checks, CI, and release builds reject binding drift. Bootstrap, doctor, and development report drift while remaining usable if otherwise compatible. None of these commands silently rewrites tracked configuration. Bootstrap never edits editor settings. Optional editor setup accepts JSON settings; if an existing settings file uses JSON with comments, it is preserved and `toolkit paths` provides the values for manual setup.
