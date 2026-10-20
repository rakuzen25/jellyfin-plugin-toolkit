import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const toolkit = fileURLToPath(new URL("../", import.meta.url));

/** Dependencies belong to the consumer's native manifest; source tests use this checkout. */
export function resolveDependency(name, entry) {
    for (const root of [process.env.JELLYFIN_PLUGIN_ROOT, toolkit].filter(Boolean)) {
        if (entry) {
            const filename = path.join(root, "node_modules", name, entry);
            if (existsSync(filename)) return filename;
        } else {
            try {
                return createRequire(path.join(root, "package.json")).resolve(name);
            } catch (error) {
                if (error.code !== "MODULE_NOT_FOUND" && error.code !== "ERR_PACKAGE_PATH_NOT_EXPORTED") throw error;
            }
        }
    }
    throw new Error("Missing toolkit dependency " + name + "; run bootstrap to restore the locked workspace.");
}

export function dependency(name) {
    return import(pathToFileURL(resolveDependency(name)).href);
}
