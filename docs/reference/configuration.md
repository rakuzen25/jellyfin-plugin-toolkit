# Consumer configuration reference

Each consumer has three primary inputs:

| Input               | Contract                                                                    |
| ------------------- | --------------------------------------------------------------------------- |
| `plugin.toml`       | Schema 1 identity, capabilities, paths, operations, and distribution policy |
| `version.txt`       | Authoritative plugin version; conflicting MSBuild versions are rejected     |
| `toolkit.lock.json` | Schema 1 repository, `v`-prefixed release version, and archive SHA-256      |

Native `.python-version`, `.node-version`, `global.json`, `package.json`, and dependency lockfiles remain authoritative for their tools. Generated MSBuild properties and frontend bindings live under the ignored `.build` directory.

## Configuration sections

| Section        | Fields and behavior                                                                                                                                          |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `plugin`       | Stable `id`, `guid`, host `assembly`, display `name`, repository, owner, description, overview, category                                                     |
| `host`         | Official immutable `image`, `abi`, `framework`, `platform`                                                                                                   |
| `images`       | Immutable `playwright` and `actionlint` images                                                                                                               |
| `backend`      | Host `entry`, named `projects`, `project_assemblies`, test project paths, source roots; optional `unmanaged_projects` retain upstream MSBuild defaults       |
| `architecture` | Exact allowed project `edges` and forbidden source tokens per directory                                                                                      |
| `frontend`     | `enabled`; optional directory, build adapter, bundle entries, admin entry/template, style/script markers, required assets, extra assets and boundary checker |
| `sandbox`      | Plugin settings-page identifier and asset-route prefix                                                                                                       |
| `browser`      | Browser test directory and group inventory path                                                                                                              |
| `source`       | Maintained `roots` and explicit additional `files`                                                                                                           |
| `validation`   | Enabled groups, check targets, default unit groups, acceptance groups, path-classification rules and optional matrix job names                               |
| `operations`   | Named command overrides with optional working directory                                                                                                      |
| `distribution` | Web ZIP capability, shipped notices, DLL license inventory, payload notices, licensed paths and web exclusions                                               |

Paths are repository-relative and cannot escape the checkout. Entrypoint keys are relative to the frontend directory; asset and distribution output names must be canonical relative paths without `..` components. Duplicate asset outputs fail the build. Frontend entries are relative to the configured frontend directory; admin templates and source inventory paths are relative to the repository. Optional layers are omitted rather than generated empty.

An operation is a table containing `commands`, a nonempty list of nonempty argv arrays, and optional repository-relative `cwd`. Supported placeholders are `{python}`, `{root}`, `{toolkit}`, `{pnpm}`, and `{dotnet}`. Arguments are passed directly without a shell. Overrides use `check-frontend`, `test-tooling`, `test-installation`, and the corresponding command/target names. Extra test arguments are forwarded to the configured command.

Unknown maintained inputs fail CI classification until a rule covers them. A toolkit update changes evidence fingerprints. Archive metadata, immutable reusable-workflow revisions and their release-version comments, and the versioned Renovate preset must agree; partial updates fail validation.

## Commands

| Command                            | Result                                                                       |
| ---------------------------------- | ---------------------------------------------------------------------------- |
| `bootstrap`                        | Verify tools/archive, restore dependencies, prepare verified host references |
| `doctor`                           | Diagnose toolchain and configuration mismatches                              |
| `dev`                              | Run the checkout-owned disposable server and supervisor                      |
| `build frontend` / `build backend` | Build one component                                                          |
| `build`                            | Capture source and build all declared distribution artifacts                 |
| `check [target]`                   | Run maintained checks without dependency installation                        |
| `format [target]`                  | Apply maintained formatting                                                  |
| `test [group]`                     | Run a group; defaults to configured unit groups                              |
| `validate --full`                  | Run configured checks, unit groups, complete packaging and acceptance groups |
| `new`                              | Generate a consumer using the installed toolkit lock                         |

Backend tests compile missing embedded frontend resources using already restored dependencies. Verified prebuilt browser assets can be supplied by CI without requiring a second frontend build.

The launcher accepts verified archives from the source bundle, cache, or explicit `JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE` path. Only bootstrap installs a missing toolkit automatically; other commands diagnose a missing installation. Corrupt, incomplete, linked, or escaping archives are rejected before replacing the active installation.

## Source and evidence boundaries

Maintained source inputs cannot be symlinks, including links to files inside the checkout. Every explicit `source.files` entry must exist. Optional source directories may be absent. Frontend sources, build adapters, templates, styles, assets, and extra assets must belong to the maintained source inventory. Add external input directories to `source.roots`, or individual inputs to `source.files`; their bytes participate in frontend evidence fingerprints.

Optional `validation.job_names` and `validation.aggregate_jobs` map enabled groups to nonempty, unique job-name lists. `validation.artifact_jobs` is also a nonempty, unique list. Empty lists cannot disable a validation gate. Release evidence must identify the latest authenticated run attempt and a successful aggregate job from that attempt.
