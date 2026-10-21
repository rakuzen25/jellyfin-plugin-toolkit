# Licensing and provenance

Original project-owned source uses Apache-2.0. Inherited source retains its existing license and attribution. The optional `src/jellyfin_plugin_toolkit/resources/ui/lifecycle.ts` and its tests are MPL-2.0; `MPL-2.0.txt` accompanies the toolkit. See [NOTICE](../../NOTICE), [Apache license](../../LICENSE), and [GPLv3 distribution material](../../GPL-3.0.txt).

Source licensing and compiled plugin distribution obligations are recorded separately. Plugins linked against Jellyfin carry applicable GPLv3 distribution material and matching source. [Jellyfin's plugin template](https://github.com/jellyfin/jellyfin-plugin-template#licensing) documents its permission for GPLv3-compatible permissive plugin source licenses. Upstream LGPL libraries retain source, replacement information and required notices; dictionary data retains its own notices.

Each consumer declares the actual distributed binary and notice inventory in `plugin.toml`. Undeclared binaries, missing notices and host DLLs in plugin payloads fail packaging. Build-only dependencies remain in native lockfiles and are distinguished from shipped dependencies. Build provenance records the pinned official host image and hashes of extracted host references.

Python build dependencies are installed by uv from each project's lockfile. Their original distribution metadata and licenses accompany their installed distributions; the toolkit does not bundle a Python runtime. These build dependencies do not become plugin runtime assemblies. Node tooling dependencies remain in consumer manifests and pnpm lockfiles.

The source ZIP includes maintained plugin inputs, `uv.lock`, the exact verified toolkit wheel and its matching source distribution. Normal reconstruction uses locked uv installation; an empty package cache may require network access. Record exceptions with SPDX license identifiers and explicit origin information; preserve inherited attribution when moving files.

See also the [Apache compatibility guidance](https://www.apache.org/foundation/license-faq.html).
