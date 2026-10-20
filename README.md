# Jellyfin Plugin Toolkit

Shared development, validation, packaging, and release tooling for independently released Jellyfin plugins. The first supported environment is Linux x64 with Docker.

Plugins own their behavior, Jellyfin registration, APIs, persistence, and application state. The toolkit supplies commands and optional bundled TypeScript primitives; it introduces no shared C# runtime.

- [Develop and generate a plugin](docs/how-to/develop.md)
- [Configuration and command reference](docs/reference/configuration.md)
- [Architecture and extension boundaries](docs/explanation/architecture.md)
- [Prepare and publish releases](docs/how-to/release.md)
- [Licensing and dependency provenance](docs/reference/licensing.md)

Original source is Apache-2.0. See [NOTICE](NOTICE) for inherited attribution and file-level exceptions, including the MPL-2.0 resource lifecycle module.

Run `pnpm check` to verify formatting and linting, and `pnpm format` to apply formatting. With the pinned Python, run `python3 scripts/runtime.py`, install the Python check requirements, and run `pnpm install --frozen-lockfile` first. Runtime preparation installs hash-locked wheels into the ignored build directory; release archives include those exact dependencies. Checks do not restore dependencies. CI runs the same check command.
