import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { rebuildQueue, proxyServer } from "../src/jellyfin_plugin_toolkit/resources/jellyfin_plugin_toolkit/dev/session.mjs";

function deferred() {
    let resolve;
    const promise = new Promise((done) => {
        resolve = done;
    });
    return { promise, resolve };
}

test("queue coalesces changes during a build and close waits for the active build", { timeout: 3000 }, async () => {
    const first = deferred(),
        second = deferred(),
        releaseFirst = deferred(),
        releaseSecond = deferred();
    const batches = [];
    const queue = rebuildQueue(
        async (batch) => {
            batches.push([...batch].sort());
            if (batches.length === 1) {
                first.resolve();
                await releaseFirst.promise;
            } else {
                second.resolve();
                await releaseSecond.promise;
            }
        },
        assert.fail,
        0,
    );
    queue.request("frontend");
    await first.promise;
    queue.request("backend");
    queue.request("backend");
    releaseFirst.resolve();
    await second.promise;
    let closed = false;
    const closing = queue.close().then(() => {
        closed = true;
    });
    await Promise.resolve();
    assert.equal(closed, false);
    releaseSecond.resolve();
    await closing;
    assert.deepEqual(batches, [["frontend"], ["backend"]]);
    queue.request("frontend");
    assert.equal(batches.length, 2);
});

test("failed inputs survive until another request and manual rebuild works while paused", { timeout: 3000 }, async () => {
    const failed = deferred(),
        complete = deferred();
    const batches = [];
    const queue = rebuildQueue(
        async (batch) => {
            batches.push([...batch].sort());
            if (batches.length === 1) throw new Error("compile failed");
            complete.resolve();
        },
        () => failed.resolve(),
        0,
        { paused: true },
    );
    queue.request("frontend");
    assert.equal(batches.length, 0);
    queue.rebuild();
    await failed.promise;
    queue.rebuild();
    await complete.promise;
    await queue.close();
    assert.deepEqual(batches, [
        ["backend", "frontend"],
        ["backend", "frontend"],
    ]);
});

test("proxy replaces assets only after a successful upstream authorization decision", { timeout: 5000 }, async (t) => {
    const upstream = http.createServer((req, res) => {
        res.writeHead(req.headers.authorization === "fixture" ? 200 : 401, { "content-type": "text/plain" });
        res.end("upstream");
    });
    await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
    t.after(() => new Promise((resolve) => upstream.close(resolve)));
    const proxy = proxyServer(
        { port: upstream.address().port, files: new Map([["bundle.js", Buffer.from("rebuilt")]]) },
        {
            frontend: { reload_assets: ["bundle.js"] },
            sandbox: { asset_prefix: "/assets/" },
        },
    );
    await new Promise((resolve) => proxy.server.listen(0, "127.0.0.1", resolve));
    t.after(() => proxy.close());
    const url = "http://127.0.0.1:" + proxy.server.address().port;
    const denied = await fetch(url + "/assets/bundle.js");
    assert.equal(denied.status, 401);
    assert.equal(await denied.text(), "upstream");
    const allowed = await fetch(url + "/assets/bundle.js", { headers: { authorization: "fixture" } });
    assert.equal(await allowed.text(), "rebuilt");
    assert.equal(allowed.headers.get("cache-control"), "no-store");
    const unrelated = await fetch(url + "/ordinary", { headers: { authorization: "fixture" } });
    assert.equal(await unrelated.text(), "upstream");
});
