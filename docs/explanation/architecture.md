# Foundation architecture

The toolkit is a build-time dependency distributed as a deterministic GitHub release archive. Each plugin keeps a small standard-library launcher, declarative inputs, native toolchain pins, and feature-specific adapters. The launcher verifies the archive SHA-256 and its complete file inventory before activating an ignored cache installation.

The standard source boundaries are `src/Core`, `src/Infrastructure`, and `src/Host`. Core owns host-independent policy, models, and effect interfaces. Infrastructure implements storage and external effects. Host owns Jellyfin entity adapters, authenticated APIs, registration, and lifecycle hooks. Plugins declare the allowed dependency graph; omitted layers are valid.

Shared Python modules receive an explicit consumer context. Consumer-dependent task, build, reference-cache, sandbox, and CI functions take `Context` as their first argument; importing a module does not select a consumer. CI derives an immutable `ValidationPolicy` for that context. Command entrypoints parse arguments before selecting the consumer and accept `main(argv=None)` without changing process arguments. Configuration generation only writes bindings; command orchestration owns pin checks. Formatting is a leaf dependency of tasks and checks. These Python APIs replace the previous implicit globals without compatibility wrappers. Shared tooling must not import either pilot plugin. Operations that cannot be represented as paths and capabilities remain small plugin-owned adapters. There is no generic inheritance or scripting framework.

Development queue, watch-policy, and proxy helpers can be imported without generated configuration. Pass configuration explicitly to `sourcePolicy`, `classifyChange`, and `proxyServer`.

Optional TypeScript modules cover resource disposal, accessible feedback and native controls. They bundle into each consumer's assets. Navigation, uncertain writes, command serialization and review ownership remain application responsibilities.

Reusable workflows retain a single unconditional `validate` aggregate. Change-aware planning conservatively executes checks when evidence is missing or unusable. Receipts bind enabled checks and toolchain/configuration fingerprints to exact artifact bytes. Publication jobs run protected shared tooling and require authenticated execution; read-only PR jobs receive no publication credentials.

Catalog policy independently specifies required checks, artifact profiles, plugin identities and approved signers. A release receipt cannot relax those requirements. Historical receipt formats remain verifiable under their existing policy.

Lifecycle disposal releases all resources before raising an `AggregateError` for cleanup failures. Repeat disposal remains harmless. Aborting a listener releases its lifecycle ownership, and an already-aborted signal creates no registration.

Shared scripts, actions, configuration presets, and UI components form the common plugin framework. Prefer a toolkit change when an improvement should become standard for plugins using that capability. Plugin-specific application behavior, runtime pins, dependency updates, repository policy, and extensions remain consumer-owned. Changes to reusable optional components justify foundation releases; compatible consumer tool updates do not require one. Toolkit tests use independent synthetic consumers rather than named downstream repositories.

Consumer-native configuration files import the presets in `configs/`, retaining editor discovery and local overrides. Shared implementation stays inside the verified release. Synchronization owns only the launcher and recognized toolkit references in consumer-owned workflows and Renovate configuration; it does not regenerate application or repository configuration. Proposed releases are verified and read as data by the base release's updater. Dependency installations and generated outputs remain outside the immutable toolkit installation.
