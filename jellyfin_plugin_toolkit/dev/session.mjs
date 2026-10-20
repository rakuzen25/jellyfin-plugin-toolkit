import http from "node:http";
import { watch, existsSync } from "node:fs";
import { readFile, mkdir, rm, mkdtemp } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
/** Coalesce editor bursts, never overlap work, and retain edits received during builds. */
export function rebuildQueue(build, report = console.error, delay = 150, { paused = false, onState = () => {} } = {}) {
    let timer,
        active,
        closed = false,
        automatic = false,
        manual = false;
    const pending = new Set();
    const notify = () => onState(paused ? (active ? "pausing" : "paused") : "enabled");
    const ready = () => manual || (!paused && automatic);
    async function drain() {
        if (active || closed || !ready()) return;
        active = (async () => {
            while (!closed && ready()) {
                const batch = new Set(pending);
                pending.clear();
                if (manual) {
                    batch.add("frontend");
                    batch.add("backend");
                }
                manual = false;
                automatic = false;
                try {
                    await build(batch);
                } catch (error) {
                    // Retain failed inputs, but wait for another request before retrying.
                    for (const kind of batch) pending.add(kind);
                    report(error);
                }
            }
        })();
        try {
            await active;
        } finally {
            active = undefined;
            notify();
        }
    }
    function schedule() {
        clearTimeout(timer);
        if (!closed && ready()) timer = setTimeout(() => void drain(), delay);
    }
    return {
        request(kind) {
            if (closed) return;
            pending.add(kind);
            automatic = true;
            schedule();
        },
        rebuild() {
            if (closed) return;
            manual = true;
            schedule();
        },
        toggle() {
            if (closed) return;
            paused = !paused;
            if (!paused && pending.size) automatic = true;
            schedule();
            notify();
        },
        async close() {
            closed = true;
            clearTimeout(timer);
            pending.clear();
            manual = false;
            automatic = false;
            await active;
        },
    };
}

/** Derive watched inputs from declared projects and assets, including path overrides. */
export function sourcePolicy(configuration) {
    const backend = configuration.backend || {};
    const projects = new Set(Object.values(backend.projects || {}));
    const visit = (project) => {
        for (const dependency of configuration.architecture?.edges?.[project] || []) {
            if (!projects.has(dependency)) {
                projects.add(dependency);
                visit(dependency);
            }
        }
    };
    for (const project of [...projects]) visit(project);
    const backendRoots = [...projects].map((project) => path.posix.dirname(project));
    const frontend = configuration.frontend || {};
    const frontendRoots = frontend.enabled
        ? [frontend.directory || "frontend", frontend.assets || (frontend.directory || "frontend") + "/assets"]
        : [];
    const frontendFiles = frontend.enabled
        ? [frontend.admin_template, frontend.admin_styles, ...(frontend.extra_assets || []).map((asset) => asset.source)].filter(Boolean)
        : [];
    const directories = [...new Set([...backendRoots, ...frontendRoots, ...frontendFiles.map((name) => path.posix.dirname(name))])].filter(
        (name) => name !== ".",
    );
    return {
        backendRoots,
        frontendRoots,
        frontendFiles,
        directories: directories.filter((name) => !directories.some((parent) => parent !== name && name.startsWith(parent + "/"))),
    };
}

export function classifyChange(filename, configuration) {
    const name = filename.replaceAll("\\", "/");
    if (/(^|\/)(node_modules|bin|obj|\.git|\.build|dist)(\/|$)/.test(name) || /\.test\.ts$/.test(name)) return null;
    const policy = sourcePolicy(configuration);
    if (policy.frontendFiles.includes(name) || policy.frontendRoots.some((directory) => name.startsWith(directory + "/"))) return "frontend";
    if (policy.backendRoots.some((directory) => name.startsWith(directory + "/")) || /^(Directory\.Build\.(props|targets)|global\.json)$/.test(name))
        return "backend";
    return null;
}

const reloadClient = `const events = new EventSource('/__jellyfin_plugin_dev/events');
let revision;
events.onmessage = ({data}) => { if (revision && revision !== data) location.reload(); revision = data; };`;

/** Preserve Jellyfin's authorization decision before replacing a successful asset/page response. */
export function proxyServer(state, configuration) {
    const rendererAssets = configuration.frontend?.reload_assets || [];
    const assetPrefix = configuration.sandbox?.asset_prefix || "/__no_plugin_assets__/";
    const settingsPage = configuration.sandbox?.settings_page || "";
    const clients = new Set();
    const sockets = new Set();
    let revision = 0;
    const server = http.createServer((req, res) => {
        const url = new URL(req.url, "http://localhost");
        if (url.pathname === "/__jellyfin_plugin_dev/events") {
            res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive" });
            res.write("data: " + revision + "\n\n");
            clients.add(res);
            req.on("close", () => clients.delete(res));
            return;
        }
        if (url.pathname === "/__jellyfin_plugin_dev/client.js") {
            res.writeHead(200, { "Content-Type": "application/javascript", "Cache-Control": "no-store" });
            res.end(reloadClient);
            return;
        }
        if (!state.port) {
            res.writeHead(503, { "Retry-After": "1" });
            res.end("Jellyfin is building or restarting; see the development terminal.");
            return;
        }
        const headers = { ...req.headers, host: "127.0.0.1:" + state.port, "accept-encoding": "identity" };
        if (url.pathname.startsWith(assetPrefix) || url.pathname.toLowerCase() === "/web/configurationpage") {
            delete headers["if-none-match"];
            delete headers["if-modified-since"];
        }
        const upstream = http.request({ hostname: "127.0.0.1", port: state.port, path: req.url, method: req.method, headers }, async (response) => {
            const asset = url.pathname.startsWith(assetPrefix) ? url.pathname.slice(assetPrefix.length) : undefined;
            const config = url.pathname.toLowerCase() === "/web/configurationpage" && url.searchParams.get("name") === settingsPage;
            const index = /^(\/web\/?|\/web\/index\.html)$/i.test(url.pathname) && String(response.headers["content-type"]).includes("text/html");
            const replace = response.statusCode === 200 && (rendererAssets.includes(asset) || config || index);
            if (!replace) {
                res.writeHead(response.statusCode, response.headers);
                response.pipe(res);
                return;
            }
            try {
                const chunks = [];
                for await (const chunk of response) chunks.push(chunk);
                let body = Buffer.concat(chunks);
                if (config) body = state.files.get("config.html") || body;
                else if (asset) body = state.files.get(asset) || body;
                else
                    body = Buffer.from(body.toString().replace(/<\/head>/i, '<script defer src="/__jellyfin_plugin_dev/client.js"></script></head>'));
                const outputHeaders = { ...response.headers, "cache-control": "no-store", "content-length": body.length };
                for (const key of ["etag", "last-modified", "content-encoding", "transfer-encoding"]) delete outputHeaders[key];
                res.writeHead(response.statusCode, outputHeaders);
                res.end(req.method === "HEAD" ? undefined : body);
            } catch (error) {
                res.destroy(error);
            }
        });
        upstream.on("error", () => {
            if (!res.headersSent) res.writeHead(502);
            res.end("Jellyfin unavailable; see the development terminal.");
        });
        req.on("aborted", () => upstream.destroy());
        req.pipe(upstream);
    });
    server.on("connection", (socket) => {
        sockets.add(socket);
        socket.on("close", () => sockets.delete(socket));
    });
    server.on("upgrade", (req, socket, head) => {
        if (!state.port) {
            socket.destroy();
            return;
        }
        const upstream = http.request({
            hostname: "127.0.0.1",
            port: state.port,
            path: req.url,
            headers: { ...req.headers, host: "127.0.0.1:" + state.port },
        });
        upstream.on("upgrade", (response, connection, upstreamHead) => {
            socket.write(
                "HTTP/1.1 101 Switching Protocols\r\n" +
                    Object.entries(response.headers)
                        .map(([key, value]) => key + ": " + value)
                        .join("\r\n") +
                    "\r\n\r\n",
            );
            if (head.length) connection.write(head);
            if (upstreamHead.length) socket.write(upstreamHead);
            connection.on("error", () => socket.destroy());
            socket.on("error", () => connection.destroy());
            socket.on("close", () => connection.destroy());
            connection.pipe(socket).pipe(connection);
        });
        upstream.on("response", (response) => {
            socket.end("HTTP/1.1 " + response.statusCode + " Rejected\r\nConnection: close\r\n\r\n");
            response.resume();
        });
        upstream.on("error", () => socket.destroy());
        upstream.end();
    });
    return {
        server,
        reload() {
            revision++;
            for (const client of clients) client.write("data: " + revision + "\n\n");
        },
        close() {
            for (const client of clients) client.end();
            for (const socket of sockets) socket.destroy();
            server.closeAllConnections();
            return new Promise((resolve) => server.close(resolve));
        },
    };
}

export async function session(argv = process.argv.slice(2)) {
    const root = process.env.JELLYFIN_PLUGIN_ROOT;
    if (!root) throw new Error("JELLYFIN_PLUGIN_ROOT is required");
    const config = JSON.parse(await readFile(path.join(root, ".build/generated/plugin.json"), "utf8"));
    let adapterPromise;
    async function frontendAdapter() {
        adapterPromise ||= (
            config.frontend.build
                ? import(pathToFileURL(path.join(root, config.frontend.build)).href)
                : import("../../node/build.mjs").then(({ createBuilder }) => createBuilder(root))
        ).then((adapter) => {
            if (typeof adapter.compileWeb !== "function" || typeof adapter.writeGeneration !== "function")
                throw new Error("The configured frontend build adapter must export compileWeb and writeGeneration");
            return adapter;
        });
        return adapterPromise;
    }
    async function compileWeb() {
        return config.frontend?.enabled ? (await frontendAdapter()).compileWeb() : new Map();
    }
    async function writeGeneration(files, directory) {
        if (config.frontend?.enabled) await (await frontendAdapter()).writeGeneration(files, directory);
        else await mkdir(directory, { recursive: true });
    }

    const publicPort = Number(argv[argv.indexOf("--port") + 1] || 18096);
    const state = { port: null, files: new Map() };
    const proxy = proxyServer(state, config);
    await new Promise((resolve, reject) => {
        proxy.server.once("error", reject);
        proxy.server.listen(publicPort, "127.0.0.1", resolve);
    });
    const url = "http://localhost:" + publicPort + "/web/";
    const directory = path.join(root, ".build/dev");
    await mkdir(directory, { recursive: true });
    const children = new Set();
    let stopping = false,
        current,
        stopPromise;
    function python(operation, extra = []) {
        return new Promise((resolve, reject) => {
            const child = spawn(
                process.env.JELLYFIN_PLUGIN_DEV_PYTHON || "python3",
                ["-m", "jellyfin_plugin_toolkit.dev.local_jellyfin", operation, ...extra],
                {
                    cwd: root,
                    stdio: "inherit",
                    detached: true,
                },
            );
            children.add(child);
            child.on("error", reject);
            child.on("exit", (code, signal) => {
                children.delete(child);
                if (code === 0) resolve();
                else reject(new Error("Sandbox " + operation + " failed (" + (signal || code) + ")"));
            });
        });
    }
    const mountArgs = argv.filter(
        (_, index) => argv[index - 1] === "--media-mount" || argv[index] === "--media-mount" || argv[index] === "--clear-media-mounts",
    );
    const open = () => {
        const child = spawn("xdg-open", [url], { stdio: "ignore" });
        child.on("error", () => console.log("Open " + url));
    };
    async function rebuild(batch) {
        const started = Date.now();
        let next = current;
        if (!current || batch.has("frontend")) {
            console.log("[dev] Building frontend…");
            const files = await compileWeb();
            if (stopping) return;
            const staged = await mkdtemp(path.join(directory, "web-"));
            await writeGeneration(files, staged);
            next = { directory: staged, files };
        }
        try {
            if (stopping) {
                if (next !== current) await rm(next.directory, { recursive: true, force: true });
                return;
            }
            if (!current || batch.has("backend")) {
                console.log("[dev] Building backend; the previous server stays available until compilation succeeds…");
                try {
                    await python("deploy", ["--web", next.directory, ...mountArgs]);
                } finally {
                    try {
                        state.port = JSON.parse(await readFile(path.join(root, ".build/local-jellyfin/session.json"), "utf8")).port;
                    } catch {
                        state.port = null;
                    }
                }
            }
            if (stopping) return;
            const previous = current;
            current = next;
            state.files = next.files;
            if (previous && previous !== next) await rm(previous.directory, { recursive: true, force: true });
            proxy.reload();
            console.log("[dev] Ready " + url + " (" + (Date.now() - started) + " ms)");
        } catch (error) {
            if (next !== current) await rm(next.directory, { recursive: true, force: true });
            throw error;
        }
    }
    let updateState = argv.includes("--no-watch") ? "paused" : "enabled";
    const queue = rebuildQueue(
        rebuild,
        (error) => {
            if (!stopping)
                console.error(
                    "[dev] " +
                        error.message +
                        (updateState === "enabled" ? "; save again or press r to retry." : "; press r to retry or p to resume."),
                );
        },
        150,
        {
            paused: argv.includes("--no-watch"),
            onState(value) {
                const changed = value !== updateState;
                updateState = value;
                if (!stopping && (changed || value === "paused")) {
                    console.log(
                        "[dev] " +
                            (value === "enabled"
                                ? "Automatic updates enabled."
                                : value === "pausing"
                                  ? "Pausing automatic updates; waiting for the current build…"
                                  : current
                                    ? "Preview stable; automatic updates paused."
                                    : "Automatic updates paused; no successful preview yet."),
                    );
                }
            },
        },
    );
    const watchers = [];
    const help = () => {
        console.log("[dev] Automatic updates " + updateState + ".");
        console.log("[dev] r rebuild/restart once · p pause/resume updates · o open browser · h help · Ctrl+C stop (sandbox data retained)");
    };
    function input(key) {
        if (key === "\u0003") void shutdown();
        else if (key.trim() === "r") {
            queue.rebuild();
        } else if (key.trim() === "p") queue.toggle();
        else if (key.trim() === "o") open();
        else if (key.trim() === "h") help();
    }
    function shutdown() {
        if (stopPromise) return stopPromise;
        stopping = true;
        stopPromise = (async () => {
            for (const watcher of watchers) watcher.close();
            for (const child of children) {
                try {
                    process.kill(-child.pid, "SIGINT");
                } catch {
                    /* Already exited. */
                }
            }
            const force = setTimeout(() => {
                for (const child of children) {
                    try {
                        process.kill(-child.pid, "SIGKILL");
                    } catch {
                        /* Already exited. */
                    }
                }
            }, 8000);
            await queue.close();
            clearTimeout(force);
            await python("stop");
            await proxy.close();
            if (process.stdin.isTTY) process.stdin.setRawMode(false);
            process.stdin.off("data", input);
            process.stdin.pause();
            if (current) await rm(current.directory, { recursive: true, force: true });
            console.log("[dev] Stopped; sandbox data retained.");
        })();
        return stopPromise;
    }
    process.once("SIGINT", () => void shutdown());
    process.once("SIGTERM", () => void shutdown());
    try {
        for (const name of sourcePolicy(config).directories.filter((name) => existsSync(path.join(root, name)))) {
            watchers.push(
                watch(path.join(root, name), { recursive: true }, (_, filename) => {
                    if (filename) {
                        const kind = classifyChange(name + "/" + filename, config);
                        if (kind) queue.request(kind);
                    }
                }),
            );
        }
        watchers.push(
            watch(root, (_, filename) => {
                if (filename && !filename.includes("/")) {
                    const kind = classifyChange(filename, config);
                    if (kind) {
                        queue.request(kind);
                        if (filename === "Directory.Build.props") queue.request("frontend");
                    }
                }
            }),
        );
        help();
        if (process.stdin.isTTY) {
            process.stdin.setRawMode(true);
            process.stdin.setEncoding("utf8");
            process.stdin.on("data", input);
            process.stdin.resume();
        }
        queue.rebuild();
        if (argv.includes("--open")) {
            const timer = setInterval(() => {
                if (state.port || stopping) {
                    clearInterval(timer);
                    if (!stopping) open();
                }
            }, 200);
        }
        await new Promise((resolve) => proxy.server.once("close", resolve));
        await stopPromise;
    } finally {
        await shutdown();
    }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    try {
        await session();
    } catch (error) {
        console.error("[dev] " + error.message);
        process.exitCode = 1;
    }
}
