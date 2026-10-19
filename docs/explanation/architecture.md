# Foundation architecture

The toolkit is a build-time dependency distributed as a deterministic GitHub release archive. Each plugin keeps a small standard-library launcher, declarative inputs, native toolchain pins, and feature-specific adapters. The launcher verifies the archive SHA-256 and its complete file inventory before activating an ignored cache installation.

The standard source boundaries are `src/Core`, `src/Infrastructure`, and `src/Host`. Core owns host-independent policy, models, and effect interfaces. Infrastructure implements storage and external effects. Host owns Jellyfin entity adapters, authenticated APIs, registration, and lifecycle hooks. Plugins declare the allowed dependency graph; omitted layers are valid.

Shared Python modules receive an explicit consumer context. Shared tooling must not import either pilot plugin. Operations that cannot be represented as paths and capabilities remain small plugin-owned adapters. There is no generic inheritance or scripting framework.

Optional TypeScript modules cover resource disposal, accessible feedback and native controls. They bundle into each consumer's assets. Navigation, uncertain writes, command serialization and review ownership remain application responsibilities.

Reusable workflows retain a single unconditional `validate` aggregate. Change-aware planning conservatively executes checks when evidence is missing or unusable. Receipts bind enabled checks and toolchain/configuration fingerprints to exact artifact bytes. Publication jobs run protected shared tooling and require authenticated execution; read-only PR jobs receive no publication credentials.

Catalog policy independently specifies required checks, artifact profiles, plugin identities and approved signers. A release receipt cannot relax those requirements. Historical receipt formats remain verifiable under their existing policy.
