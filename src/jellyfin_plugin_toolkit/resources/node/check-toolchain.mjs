import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function verifyVersions(manifest, nodePin, nodeVersion, pnpmVersion) {
    const pin = manifest.packageManager?.match(/^pnpm@(\d+\.\d+\.\d+)$/)?.[1];
    if (!pin || manifest.engines?.pnpm !== pin) throw new Error("packageManager and engines.pnpm must pin the same exact pnpm version.");
    if (manifest.engines?.node !== nodePin) throw new Error(".node-version and engines.node must match.");
    if (nodeVersion !== nodePin) throw new Error(`Node ${nodePin} required; found ${nodeVersion}.`);
    if (pnpmVersion !== pin) throw new Error(`pnpm ${pin} required; found ${pnpmVersion}.`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const index = process.argv.indexOf("--root");
    const root = index < 0 ? process.env.JELLYFIN_PLUGIN_ROOT || process.cwd() : path.resolve(process.argv[index + 1]);
    const manifest = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
    const nodePin = readFileSync(path.join(root, ".node-version"), "utf8").trim();
    const pnpmVersion = execFileSync(process.env.JELLYFIN_PLUGIN_PNPM || "pnpm", ["--version"], { encoding: "utf8" }).trim();
    verifyVersions(manifest, nodePin, process.versions.node, pnpmVersion);
    console.log(`Verified Node ${nodePin} and pnpm ${pnpmVersion}.`);
}
