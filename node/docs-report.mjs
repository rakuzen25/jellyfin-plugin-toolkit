import path from "node:path";

/** Keep navigation reachability separate from Markdown syntax and link validation. */
export default function report(files) {
    const root = process.cwd();
    const errors = files.flatMap((file) =>
        file.messages.map((message) => `${path.relative(root, file.path)}:${message.line || 1}: ${message.reason}`),
    );
    const edges = new Map(files.filter((file) => file.data.toolkitLinks).map((file) => [path.resolve(root, file.path), file.data.toolkitLinks]));
    const readme = path.join(root, "README.md");
    const reached = new Set();
    const pending = [readme];
    while (pending.length) {
        const name = pending.pop();
        if (reached.has(name)) continue;
        reached.add(name);
        pending.push(...(edges.get(name) || []));
    }
    if (!edges.has(readme)) errors.push("README.md: missing documentation entrypoint");
    for (const name of edges.keys()) {
        if (name.startsWith(path.join(root, "docs") + path.sep) && !reached.has(name))
            errors.push(path.relative(root, name) + ": unreachable from README.md");
    }
    return JSON.stringify(errors);
}
