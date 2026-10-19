import assert from "node:assert/strict";
import test from "node:test";
import { sourcePolicy, classifyChange } from "../jellyfin_plugin_toolkit/dev/session.mjs";

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
