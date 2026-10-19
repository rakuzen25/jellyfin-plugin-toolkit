import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createBuilder } from "../node/build.mjs";

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
