import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const root = await mkdtemp(path.join(os.tmpdir(), "toolkit-watch-policy-"));
await mkdir(path.join(root, ".build/generated"), { recursive: true });
await writeFile(path.join(root, ".build/generated/plugin.json"), JSON.stringify({ frontend: { enabled: false } }));
process.env.JELLYFIN_PLUGIN_ROOT = root;
const { sourcePolicy, classifyChange } = await import("../jellyfin_plugin_toolkit/dev/session.mjs");
await rm(root, { recursive: true, force: true });

test("backend-only plugins watch declared project directories without requiring optional layers", () => {
    const config = { backend: { projects: { Host: "server/Entry.csproj" } }, frontend: { enabled: false } };
    assert.deepEqual(sourcePolicy(config).directories, ["server"]);
    assert.equal(classifyChange("server/Plugin.cs", config), "backend");
    assert.equal(classifyChange("frontend/example.ts", config), null);
    assert.equal(classifyChange("server/obj/generated.cs", config), null);
});

test("path overrides and declared project dependencies determine watch classification", () => {
    const config = {
        backend: { projects: { Host: "server/Entry.csproj", Core: "logic/Domain.csproj" } },
        architecture: { edges: { "server/Entry.csproj": ["third-party/Helper/Helper.csproj"] } },
        frontend: {
            enabled: true,
            directory: "ui/adminapp",
            assets: "resources",
            admin_template: "templates/page.html",
            extra_assets: [{ source: "legal/NOTICE", name: "NOTICE" }],
        },
    };
    for (const name of ["server/Plugin.cs", "logic/Policy.cs", "third-party/Helper/Helper.cs"]) assert.equal(classifyChange(name, config), "backend");
    for (const name of ["ui/adminapp/main.ts", "resources/icon.svg", "templates/page.html", "legal/NOTICE"])
        assert.equal(classifyChange(name, config), "frontend");
    for (const name of ["vendor/Undeclared/Helper.cs", "tests/backend/Tests.cs", "ui/adminapp/main.test.ts", "src/Plugin.cs"])
        assert.equal(classifyChange(name, config), null);
    assert.ok(sourcePolicy(config).directories.includes("legal"));
});
