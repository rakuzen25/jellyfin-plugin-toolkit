# Jellyfin Plugin Toolkit

Shared development, validation, packaging, and release tooling for independently released Jellyfin plugins. The first supported environment is Linux x64 with Docker.

Plugins own their behavior, Jellyfin registration, APIs, persistence, and application state. The toolkit supplies commands and optional bundled TypeScript primitives; it introduces no shared C# runtime.

- [Develop and generate a plugin](docs/how-to/develop.md)
- [Configuration and command reference](docs/reference/configuration.md)
- [Architecture and extension boundaries](docs/explanation/architecture.md)
- [Prepare and publish releases](docs/how-to/release.md)
- [Licensing and dependency provenance](docs/reference/licensing.md)

Original source is Apache-2.0. See [NOTICE](NOTICE) for inherited attribution and file-level exceptions, including the MPL-2.0 resource lifecycle module.

Install uv at the version required by `pyproject.toml`, plus the pinned Node and pnpm versions. uv downloads the pinned Python automatically.

```sh
uv sync --locked
pnpm install --frozen-lockfile
uv run jpt prepare
pnpm check
uv run python -m unittest discover -s tests
uv build
```

Use `uv run jpt …` in consumer repositories. Local commands synchronize their Python environment automatically; CI uses committed lockfiles and `uv run --no-sync`. See the development guide for candidate wheels and editable development.
