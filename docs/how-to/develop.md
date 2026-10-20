# Develop a plugin

Install the versions of Python, Node, pnpm, and the .NET SDK declared by the plugin's native pin files. Docker must be available to your user. The supported host is Linux x64.

From an existing consumer checkout:

```sh
./plugin bootstrap
./plugin doctor
./plugin dev --port 18096
```

Bootstrap verifies the locked toolkit archive, restores frozen pnpm and NuGet dependencies, and extracts references from the pinned official Jellyfin image. The server owns a disposable configuration directory for this checkout. Its endpoint binds to loopback. Optional `--media-mount /absolute/media/path:/media` mounts are read-only.

Use the interactive supervisor's rebuild and pause controls during development. Backend replacements compile before the working server stops; failed replacements restore the previous plugin. Pending changes survive pause/resume.

Run component checks before a complete acceptance run:

```sh
./plugin check
./plugin test
./plugin validate --full
```

Checks require prepared dependencies and never install them. Full validation builds the complete distribution and executes the consumer's enabled acceptance operations, including installation and reproducibility.

## Generate a repository

From a toolkit source checkout with its pinned Python available:

```sh
./toolkit new /tmp/example-plugin --id example-plugin --name 'Example Plugin' --repository example/example-plugin --admin
```

Replace `example/example-plugin` with your destination repository. Omit `--admin` for a backend-only plugin. Use `--host-project server/Entry.csproj` to exercise a different backend path. The destination must not exist. The generated GUID is unique and must remain stable after publication.

The generated consumer obtains its locked release on `./plugin bootstrap`. For an unpublished candidate, supply the exact candidate archive through `JELLYFIN_PLUGIN_TOOLKIT_ARCHIVE`; its checksum must still match `toolkit.lock.json`. A source distribution includes that archive at `build-support/toolkit.tar.gz`.

## Adopt a foundation update

Review the toolkit dependency PR, then preview and apply its binding changes against the base branch:

```sh
./plugin toolkit sync --base-ref main --download
./plugin toolkit sync --base-ref main --download --apply
./plugin bootstrap
./plugin toolkit sync --check
./plugin check
./plugin test
```

The base branch must already use a release with binding synchronization. For the first migration, explicitly adopt the verified release's launcher and native preset imports, preserve consumer overrides, then run `./plugin toolkit sync --check`. Do not merge an adoption that points to a local or unpublished candidate. Publish and verify the release using the release procedure first.

Compatible updates to consumer tools remain ordinary consumer dependency updates. If a fix or default should apply to other plugins using the same capability, prefer implementing it in the toolkit rather than maintaining copies or permanent consumer workarounds.

Editors continue reading consumer-native configuration files, which inherit the shared defaults after bootstrap. To inspect tool paths or opt into VS Code setup:

```sh
./plugin toolkit paths
./plugin toolkit editor vscode
./plugin toolkit editor vscode --apply
```
