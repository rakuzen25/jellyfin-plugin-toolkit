import assert from "node:assert/strict";
import { test } from "node:test";
import http from "node:http";
import net from "node:net";
import { once } from "node:events";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { gzipSync } from "node:zlib";
import { rebuildQueue, classifyChange, proxyServer, session } from "../jellyfin_plugin_toolkit/dev/session.mjs";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function listen(server) {
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    return server.address().port;
}

test("watch queue batches bursts, serializes work and processes edits received during a failed build", async () => {
    const batches = [],
        errors = [];
    let release;
    const gate = new Promise((resolve) => {
        release = resolve;
    });
    const queue = rebuildQueue(
        async (batch) => {
            batches.push([...batch].sort());
            if (batches.length === 1) {
                await gate;
                throw new Error("compile failure");
            }
        },
        (error) => errors.push(error.message),
        1,
    );
    queue.request("frontend");
    queue.request("backend");
    await delay(20);
    queue.request("frontend");
    queue.request("frontend");
    await delay(20);
    assert.equal(batches.length, 1);
    release();
    await delay(20);
    await queue.close();
    assert.deepEqual(batches, [
        ["backend", "frontend"],
        ["backend", "frontend"],
    ]);
    assert.deepEqual(errors, ["compile failure"]);
    queue.request("backend");
    await delay(5);
    assert.equal(batches.length, 2);
});

test("paused startup builds once, holds edits and resumes only changed parts", async () => {
    const batches = [];
    const queue = rebuildQueue(async (batch) => batches.push([...batch].sort()), assert.fail, 1, { paused: true });
    queue.rebuild();
    await delay(20);
    assert.deepEqual(batches, [["backend", "frontend"]]);
    queue.toggle();
    await delay(20);
    assert.equal(batches.length, 1, "resume without edits does no work");
    queue.toggle();
    queue.request("frontend");
    queue.request("frontend");
    await delay(20);
    assert.equal(batches.length, 1);
    queue.toggle();
    await delay(20);
    assert.deepEqual(batches[1], ["frontend"]);
    await queue.close();
});

test("pause holds debounced work and waits for the active build before reporting stable", async () => {
    const batches = [],
        states = [];
    let release;
    const gate = new Promise((resolve) => {
        release = resolve;
    });
    const queue = rebuildQueue(
        async (batch) => {
            batches.push([...batch].sort());
            if (batches.length === 1) await gate;
        },
        assert.fail,
        1,
        { onState: (state) => states.push(state) },
    );
    queue.request("frontend");
    queue.toggle();
    await delay(20);
    assert.equal(batches.length, 0);
    queue.toggle();
    await delay(20);
    queue.request("backend");
    queue.toggle();
    assert.equal(states.at(-1), "pausing");
    release();
    await delay(20);
    assert.equal(states.at(-1), "paused");
    assert.equal(batches.length, 1);
    queue.toggle();
    await delay(20);
    assert.deepEqual(batches, [["frontend"], ["backend"]]);
    await queue.close();
});

test("manual refresh stays paused, clears incorporated changes and retains edits during the build", async () => {
    const batches = [];
    let release;
    const gate = new Promise((resolve) => {
        release = resolve;
    });
    const queue = rebuildQueue(
        async (batch) => {
            batches.push([...batch].sort());
            if (batches.length === 2) await gate;
        },
        assert.fail,
        1,
        { paused: true },
    );
    queue.request("frontend");
    queue.rebuild();
    await delay(20);
    queue.toggle();
    await delay(20);
    assert.equal(batches.length, 1, "manual refresh consumed the pending edit");
    queue.toggle();
    queue.rebuild();
    await delay(20);
    queue.request("frontend");
    release();
    await delay(20);
    assert.equal(batches.length, 2, "manual refresh leaves automatic updates paused");
    queue.toggle();
    await delay(20);
    assert.deepEqual(batches[2], ["frontend"]);
    await queue.close();
});

test("failed paused builds retain inputs without retrying until resumed; close discards held work", async () => {
    const batches = [],
        errors = [];
    const queue = rebuildQueue(
        async (batch) => {
            batches.push([...batch].sort());
            if (batches.length === 1) throw new Error("broken");
        },
        (error) => errors.push(error.message),
        1,
        { paused: true },
    );
    queue.rebuild();
    await delay(20);
    assert.equal(batches.length, 1);
    assert.deepEqual(errors, ["broken"]);
    queue.toggle();
    await delay(20);
    assert.deepEqual(batches, [
        ["backend", "frontend"],
        ["backend", "frontend"],
    ]);
    queue.toggle();
    queue.request("backend");
    await queue.close();
    queue.toggle();
    queue.rebuild();
    await delay(20);
    assert.equal(batches.length, 2);
});

test("shutdown waits for the active build and drops edits and manual requests queued behind it", async () => {
    let release,
        builds = 0,
        closed = false;
    const gate = new Promise((resolve) => {
        release = resolve;
    });
    const queue = rebuildQueue(
        async () => {
            builds++;
            await gate;
        },
        assert.fail,
        1,
        { paused: true },
    );
    queue.rebuild();
    await delay(20);
    queue.request("frontend");
    queue.rebuild();
    const closing = queue.close().then(() => {
        closed = true;
    });
    await delay(20);
    assert.equal(closed, false);
    release();
    await closing;
    assert.equal(builds, 1);
    assert.equal(closed, true);
});

test("watch inputs exclude tests, dependencies and generated trees", () => {
    const configuration = {
        frontend: { enabled: true },
        backend: {
            projects: {
                Host: "src/Host/Entry.csproj",
                Helper: "vendor/Helper/Helper.csproj",
            },
        },
    };
    for (const name of ["frontend/admin/config.ts", "frontend/admin/config.html", "frontend/assets/theme.css"])
        assert.equal(classifyChange(name, configuration), "frontend");
    for (const name of ["src/Host/Api/Api.cs", "src/Host/packages.lock.json", "vendor/Helper/Helper.csproj", "Directory.Build.props"])
        assert.equal(classifyChange(name, configuration), "backend");
    for (const name of ["frontend/foo.test.ts", "frontend/node_modules/a.ts", "src/Host/obj/generated.cs", ".build/dev/a.js"])
        assert.equal(classifyChange(name, configuration), null);
});

test("proxy preserves authentication/status, supplies live assets, injects reload only into the entry page and forwards WebSockets", async (t) => {
    const upstream = http.createServer((req, res) => {
        if (req.url.includes("ConfigurationPage") && req.headers.authorization !== "fixture") {
            res.writeHead(401);
            res.end("unauthorized");
            return;
        }
        const body = gzipSync("<head></head><body>embedded</body>");
        res.writeHead(200, {
            "Content-Type": req.url.startsWith("/web/") ? "text/html" : "application/javascript",
            "Content-Encoding": "gzip",
            "Content-Length": body.length,
            ETag: "old",
        });
        res.end(body);
    });
    upstream.on("upgrade", (req, socket) => {
        socket.write("HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n");
        socket.pipe(socket);
    });
    const port = await listen(upstream);
    const state = {
        port,
        files: new Map([
            ["config.html", Buffer.from("live config")],
            ["example.js", Buffer.from("live script")],
        ]),
    };
    const proxy = proxyServer(state, {
        frontend: { reload_assets: ["config.html", "example.js"] },
        sandbox: { asset_prefix: "/Example/assets/", settings_page: "example" },
    });
    const proxyPort = await listen(proxy.server);
    t.after(async () => {
        await proxy.close();
        upstream.closeAllConnections();
        await new Promise((resolve) => upstream.close(resolve));
    });
    const base = "http://127.0.0.1:" + proxyPort;
    const denied = await fetch(base + "/web/ConfigurationPage?name=example");
    assert.equal(denied.status, 401);
    assert.equal(await denied.text(), "unauthorized");
    const config = await fetch(base + "/web/ConfigurationPage?name=example", { headers: { authorization: "fixture" } });
    assert.equal(await config.text(), "live config");
    assert.equal(config.headers.get("etag"), null);
    const script = await fetch(base + "/Example/assets/example.js?v=old");
    assert.equal(await script.text(), "live script");
    assert.equal(script.headers.get("cache-control"), "no-store");
    assert.equal(script.headers.get("content-encoding"), null);
    const head = await fetch(base + "/Example/assets/example.js", { method: "HEAD" });
    assert.equal(head.status, 200);
    assert.equal(head.headers.get("content-length"), String(Buffer.byteLength("live script")));
    assert.equal(head.headers.get("etag"), null);
    assert.equal(head.headers.get("cache-control"), "no-store");
    assert.equal(await head.text(), "");
    const index = await fetch(base + "/web/index.html");
    assert.match(await index.text(), /__jellyfin_plugin_dev\/client.js/);
    const other = await fetch(base + "/web/other.html");
    assert.doesNotMatch(await other.text(), /__jellyfin_plugin_dev/);
    const events = await fetch(base + "/__jellyfin_plugin_dev/events");
    const reader = events.body.getReader();
    assert.match(Buffer.from((await reader.read()).value).toString(), /data: 0/);
    proxy.reload();
    assert.match(Buffer.from((await reader.read()).value).toString(), /data: 1/);
    await reader.cancel();
    const socket = net.connect(proxyPort, "127.0.0.1");
    await once(socket, "connect");
    socket.write("GET /socket HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n");
    assert.match((await once(socket, "data"))[0].toString(), /101 Switching Protocols/);
    socket.write("hello");
    assert.equal((await once(socket, "data"))[0].toString(), "hello");
    socket.destroy();
    state.port = null;
    assert.equal((await fetch(base + "/health")).status, 503);
});

test("occupied dev port fails before any sandbox operation", async (t) => {
    const root = await mkdtemp(path.join(os.tmpdir(), "toolkit-session-"));
    await mkdir(path.join(root, ".build/generated"), { recursive: true });
    await writeFile(path.join(root, ".build/generated/plugin.json"), JSON.stringify({ frontend: { enabled: false }, sandbox: {} }));
    const previous = process.env.JELLYFIN_PLUGIN_ROOT;
    process.env.JELLYFIN_PLUGIN_ROOT = root;
    t.after(async () => {
        if (previous === undefined) delete process.env.JELLYFIN_PLUGIN_ROOT;
        else process.env.JELLYFIN_PLUGIN_ROOT = previous;
        await rm(root, { recursive: true, force: true });
    });
    const occupied = http.createServer();
    const port = await listen(occupied);
    t.after(() => new Promise((resolve) => occupied.close(resolve)));
    await assert.rejects(session(["--port", String(port)]), { code: "EADDRINUSE" });
});
