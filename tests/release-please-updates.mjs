// Run against the dependencies locked by the immutable Release Please action.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const toolkit = fileURLToPath(new URL("..", import.meta.url));
const runtime = process.env.RELEASE_PLEASE_RUNTIME;
assert.ok(runtime, "Set RELEASE_PLEASE_RUNTIME to the pinned action checkout after npm ci");
const require = createRequire(path.resolve(runtime, "package.json"));
const { GenericJson } = require("release-please/build/src/updaters/generic-json.js");
const { Version } = require("release-please/build/src/version.js");
const locked = JSON.parse(readFileSync(path.resolve(runtime, "package-lock.json")));
assert.equal(require("release-please/package.json").version, locked.packages["node_modules/release-please"].version);
const root = mkdtempSync(path.join(tmpdir(), "toolkit-release-update-"));
const python = process.env.JELLYFIN_PLUGIN_PYTHON || "python3";
function runPython(code) {
    execFileSync(python, ["-c", code, root], {
        cwd: toolkit,
        env: { ...process.env, PYTHONPATH: [toolkit, path.join(toolkit, "tests")].join(path.delimiter) },
        stdio: "inherit",
    });
}
try {
    runPython(`
import sys
from pathlib import Path
from test_release_configuration import fixture
from jellyfin_plugin_toolkit.release.configuration import configure
root = Path(sys.argv[1])
fixture(root)
configure(root, apply=True)
`);
    const config = JSON.parse(readFileSync(path.join(root, "release-please-config.json")));
    let checked = 0;
    for (const rule of config.packages["."]["extra-files"] ?? []) {
        if (typeof rule !== "object") continue;
        const filename = path.join(root, rule.path);
        const before = JSON.parse(readFileSync(filename));
        const afterText = new GenericJson(rule.jsonpath, Version.parse("0.1.1")).updateContent(JSON.stringify(before, null, 2));
        const after = JSON.parse(afterText);
        const expected = structuredClone(before);
        for (const projects of Object.values(expected.dependencies)) {
            for (const record of Object.values(projects)) {
                for (const [name, range] of Object.entries(record.dependencies ?? {})) {
                    if (record.type === "Project" && name !== "External.Package") {
                        assert.equal(range, "[0.1.0, )");
                        record.dependencies[name] = "[0.1.1, )";
                        checked++;
                    }
                }
            }
        }
        assert.deepEqual(after, expected, `Internal ranges update and external dependencies stay unchanged: ${rule.path}`);
        writeFileSync(filename, afterText);
    }
    assert.ok(checked >= 24, "Exercise application, test, custom-root and host references across frameworks");
    writeFileSync(path.join(root, "version.txt"), "0.1.1\n");
    runPython(`
import sys
from pathlib import Path
from jellyfin_plugin_toolkit.release.configuration import check
from jellyfin_plugin_toolkit.release.__main__ import check_identity
root = Path(sys.argv[1])
check(root)
assert check_identity(root) == 'v0.1.1'
`);
    console.log(`PASS: pinned Release Please updater changed ${checked} internal ranges and preserved external dependencies`);
} finally {
    rmSync(root, { recursive: true, force: true });
}
