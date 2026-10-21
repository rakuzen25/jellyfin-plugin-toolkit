import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createBuilder } from "../src/jellyfin_plugin_toolkit/resources/node/build.mjs";

test("asset compilation preserves nested files and rejects collisions and escaping outputs", async (t) => {
    const root = await mkdtemp(path.join(os.tmpdir(), "toolkit-assets-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    await mkdir(path.join(root, ".build/generated"), { recursive: true });
    await mkdir(path.join(root, "frontend/assets/icons"), { recursive: true });
    await writeFile(path.join(root, "frontend/assets/icons/mark.svg"), "original");
    await writeFile(path.join(root, "NOTICE"), "notice");
    async function builder(extra_assets) {
        await writeFile(path.join(root, ".build/generated/plugin.json"), JSON.stringify({ frontend: { extra_assets } }));
        return createBuilder(root);
    }
    const valid = await builder([{ source: "NOTICE", name: "NOTICE" }]);
    const files = await valid.compileWeb();
    assert.equal(files.get("icons/mark.svg").toString(), "original");
    assert.equal(files.get("NOTICE").toString(), "notice");
    const duplicate = await builder([{ source: "NOTICE", name: "icons/mark.svg" }]);
    await assert.rejects(duplicate.compileWeb(), /Duplicate asset output/);
    const escaping = await builder([{ source: "NOTICE", name: "../outside" }]);
    await assert.rejects(escaping.compileWeb(), /Invalid asset output/);
});

test("failed compilation preserves the previous production generation without injecting development code", async (t) => {
    const root = await mkdtemp(path.join(os.tmpdir(), "toolkit-build-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    for (const directory of [".build/generated", "frontend/node_modules/esbuild"]) await mkdir(path.join(root, directory), { recursive: true });
    await writeFile(path.join(root, "frontend/package.json"), "{}");
    await writeFile(
        path.join(root, "frontend/node_modules/esbuild/index.js"),
        `
const fs = require('node:fs'), path = require('node:path');
exports.build = async options => {
    const text = fs.readFileSync(path.join(options.absWorkingDir, options.entryPoints[0]), 'utf8');
    if (text === 'invalid') throw Error('compile failed');
    return { outputFiles: [{ text, contents: Buffer.from(text) }] };
};
`,
    );
    await writeFile(
        path.join(root, ".build/generated/plugin.json"),
        JSON.stringify({
            frontend: {
                enabled: true,
                entries: { "main.ts": "main.js" },
            },
        }),
    );
    const input = path.join(root, "frontend/main.ts");
    await writeFile(input, "console.log('production')");
    const builder = await createBuilder(root);
    await builder.buildWeb();
    const output = path.join(root, "dist/web/main.js");
    const previous = await readFile(output, "utf8");
    assert.doesNotMatch(previous, /__jellyfin_plugin_dev|EventSource/);
    await writeFile(input, "invalid");
    await assert.rejects(builder.buildWeb(), /compile failed/);
    assert.equal(await readFile(output, "utf8"), previous);
});
