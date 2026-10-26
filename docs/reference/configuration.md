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
after = [["{python}", "-m", "scripts.check_boundaries"]]
```

The optional repository-relative `cwd` applies only to declared commands. Supported placeholders are `{python}`, `{root}`, `{toolkit}`, `{pnpm}`, and `{dotnet}`. Arguments are passed directly without a shell. Operation names include `check-frontend`, `test-tooling`, `test-installation`, and the corresponding command/target names. Extra test arguments reach the standard operation or replacement commands, never the `after` commands. Standard tooling tests discover Python tests under `tests/tooling` when that directory exists; consumers without tooling tests do not need a synthetic framework test.

Unknown maintained inputs fail CI classification until a rule covers them. A toolkit update changes evidence fingerprints. The locked wheel version, immutable reusable-workflow revisions and their release-version comments, and the versioned Renovate preset must agree; partial updates fail validation.

### Fixture-only browser result reuse

An optional `validation.browser_inputs.<group>` table declares `files` (maintained test, fixture, and configuration patterns), `assets` (built `dist/web` patterns), `projects`, `workers`, and `runner` (the GitHub runner label). Every pattern must match at least one file. Use this only for tests whose runtime inputs are fully represented by those assets and fixtures. Suites using a live application server retain ordinary fingerprints.

Planning builds the frontend before looking for reusable results. Result identity covers emitted asset bytes, declared test inputs, browser image, command, project/worker settings, and runner image. Broad impact rules still select checks, but unrelated source changes need not invalidate a fixture-only result. Browser jobs verify planned assets and execution settings before testing. Missing or expired evidence executes checks, and explicit full validation bypasses reuse. Receipt schema changes invalidate earlier evidence.

## Commands

| Command                            | Result                                                                    |
| ---------------------------------- | ------------------------------------------------------------------------- |
| `prepare`                          | Export wheel resources under `.build/jpt` without installing packages     |
| `bootstrap`                        | Restore Node and .NET dependencies and prepare verified host references   |
| `doctor`                           | Diagnose toolchain and configuration mismatches                           |
| `dev`                              | Run the checkout-owned disposable server and supervisor                   |
| `build frontend` / `build backend` | Build one component                                                       |
| `build`                            | Capture source and build all declared distribution artifacts              |
| `check [target]`                   | Run maintained checks                                                     |
| `format [target]`                  | Apply maintained formatting                                               |
| `test [group] [-- runner-args]`    | Run a group; defaults to configured unit groups                           |
| `validate`                         | Run configured checks, unit groups, packaging and acceptance groups       |
| `toolkit sync [--check / --apply]` | Preview, check or apply native lock and toolkit-reference synchronization |
| `toolkit sync --base-ref <ref>`    | Use an authoritative Git baseline for a partial update                    |
| `toolkit recover`                  | Restore an interrupted transaction while preserving newer edits           |
| `paths`                            | Print stable consumer tool and toolkit paths as JSON                      |
| `editor vscode [--apply]`          | Preview or apply VS Code settings while preserving unrelated settings     |
| `new`                              | Generate an independent uv consumer using the installed toolkit version   |

Prefix each command with `uv run jpt`. Global `--root PATH` and `--debug` precede the command. Root selection uses `--root`, then `JELLYFIN_PLUGIN_ROOT`, then the current directory. `--version` and help require no consumer checkout; `internal --help` lists workflow helpers. Shell completion is available through `--show-completion` and `--install-completion`; installation runs only when explicitly requested. CI first runs `uv sync --locked`, then uses `uv run --no-sync jpt`.
Backend tests compile missing embedded frontend resources using already restored dependencies. Verified prebuilt browser assets can be supplied by CI without requiring a second frontend build.

The toolkit dependency uses a canonical GitHub release wheel URL. uv verifies its locked SHA256 during installation. Local candidate wheels are supported only for isolated validation. Published builds require released URLs; source bundles include the exact wheel and matching source distribution.

## Source and evidence boundaries

Maintained source inputs cannot be symlinks, including links to files inside the checkout. Every explicit `source.files` entry must exist. Optional source directories may be absent. Frontend sources, build adapters, templates, styles, assets, and extra assets must belong to the maintained source inventory. Add external input directories to `source.roots`, or individual inputs to `source.files`; their bytes participate in frontend evidence fingerprints.

Standard impact rules come from the toolkit and cover configured backend/frontend paths, native toolchains, documentation, automation and tooling. `validation.rules` adds repository-specific paths and obligations; it cannot remove standard obligations. Unclassified inputs fail validation. The shared quality job owns documentation plus selected automation and tooling checks, with separate step outcomes and authenticated evidence for each logical group.

Optional `validation.job_names` and `validation.aggregate_jobs` map enabled groups to nonempty, unique job-name lists. `validation.artifact_jobs` is also a nonempty, unique list. Empty lists cannot disable a validation gate. Release evidence must identify the latest authenticated run attempt and a successful aggregate job from that attempt.

## Shared presets and synchronization

Keep native configuration entrypoints in the consumer so editors and tools discover them normally: `eslint.config.mjs` passes its native ESLint dependency imports to `createConfig({ js, defineConfig, prettier, globals, tseslint })` from `.build/jpt/configs/eslint.mjs`; `[tool.ruff]` extends `.build/jpt/configs/ruff.toml`; `.prettierrc.mjs` imports `.build/jpt/configs/prettier.mjs`; frontend TypeScript configuration extends `../.build/jpt/configs/tsconfig.json`. Keep file-selection paths, language-version selections, and plugin-specific overrides in those native files. Native dependency imports remain visible to editors and dependency analyzers; the factory contains the shared rules. Consumer dependency manifests and lockfiles remain authoritative.

Python tooling checks run Ruff defaults, strict mypy, and strict basedpyright. `[tool.basedpyright]` extends `.build/jpt/configs/basedpyright.toml`; its paths, Python version, and environment remain native repository settings. The shared mypy policy is materialized into `[tool.mypy]` by `jpt toolkit sync`; checks reject stale policy without editing tracked files. Keep mypy file selection, plugins, and import paths local. After restoring dependencies, run `jpt prepare` once before direct checker or editor use.

Repositories without `plugin.toml` use the same `jpt check [tooling|docs]` and `jpt format [tooling|docs]` commands. Declare their applicable checks in native Python metadata:

```toml
[tool.jpt.quality]
checks = ["python", "javascript", "docs", "workflows", "renovate"]
```

Supported checks are `python`, `javascript`, `typescript`, `docs`, `workflows`, and `renovate`. TypeScript requires `typescript_projects`, a list of native tsconfig paths. The toolkit owns the digest-pinned actionlint and Renovate runtimes; repository overrides are rejected. Optional `renovate_tests` names a repository assertion module run once by `check automation` through the shared offline harness. Import its Renovate helpers from `.build/jpt/node/renovate-api.mjs`, without relying on container installation paths. Optional `workflow_ignores` contains narrowly scoped actionlint diagnostic patterns for syntax supported by GitHub but not the pinned validator. Formatting uses native presets and ignores; `format` does not run type checkers. `jpt test advisories` applies the same report-only default to standalone repositories.

Standalone consumers may install the canonical toolkit wheel in the `dev` dependency group. Synchronization preserves that placement and updates native typing policy, wheel locks, workflow references, and Renovate presets together. Resource preparation does not require plugin metadata or install packages.

Synchronization preserves workflow triggers, permissions, schedules, extra jobs, Renovate policy and surrounding formatting. It updates the native toolkit dependency and lockfile, static toolkit `uses` references, their release-version comments and existing top-level toolkit presets. Ambiguous syntax, drift and unavailable releases stop without partial changes. Interrupted writes retain a recovery journal under `.build`; `toolkit recover` refuses to discard newer edits.

Use `--base-ref REF` for an authoritative Git baseline or a partially updated PR. The trusted updater reads release metadata without importing proposed toolkit code and invokes uv with source builds disabled. Every target must be a published canonical wheel. Checks, CI and release builds reject mismatched wheel versions, workflow references and presets.

Bootstrap never edits editor settings. Optional editor setup accepts JSON settings; existing JSON-with-comments settings remain intact, and `paths` provides values for manual setup.

## Advisory waivers

Advisory findings are warnings by default and remain visible in logs, the CI summary, and `dist/evidence/advisories.json`. Set `fail_on_findings = true` under `[advisories]` to make unwaived findings with published fixes fail validation. Audit service, configuration, and metadata errors fail in either mode.

Optional `[[advisories.waivers]]` entries waive a fixable finding for one ecosystem, package, exact installed version, and GHSA. Use `ecosystem = "npm"` or `"nuget"`, `package`, `version`, `ghsa`, a nonblank `reason`, an HTTPS tracking `issue` URL, and UTC `issued_at` and `expires_at` timestamp strings. Expiry must follow issuance by no more than 30 days. NuGet package names match without case sensitivity.

Review waiver changes through ordinary PRs. Expired waivers cannot suppress findings; unused and expired entries produce cleanup warnings. Renewals require reviewed timestamp changes. Waivers cannot suppress unknown fix status or audit failures. Native pnpm audit exclusions and NuGet audit suppressions are rejected so this configuration remains the sole exception mechanism.

A published fix counts even when it is a major upgrade, prerelease, or requires a transitive override. Dependency release-age restrictions still apply when installing that fix. No-fix findings need no waiver; a subsequent fresh audit blocks once upstream reports a published fix only when `fail_on_findings` is enabled. The daily check evaluates expiry even without repository changes.

The toolkit repository has no consumer `plugin.toml`; its own Node audit accepts `fail_on_findings` under `[tool.jpt.advisories]` and the same waiver fields under `[[tool.jpt.advisories.waivers]]` in `pyproject.toml`.

The standard check and format targets are `backend`, `frontend`, `tooling`, `automation`, and `docs`. Include `automation` in the plugin validation check targets; the metadata CI job invokes it for workflow, pin, Renovate, and automation identity validation. Standalone repositories also accept `check automation` and `format automation`, using their configured applicable validators.
