import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
export function verifyBrowserImage(declared, installed, image) {
    const version = image.match(/^mcr\.microsoft\.com\/playwright:v(\d+\.\d+\.\d+)-noble@sha256:[a-f0-9]{64}$/)?.[1];
    if (!version || version !== declared || version !== installed)
        throw Error("Playwright manifest, installed package and digest-pinned noble image must have identical versions.");
}
export function checkBrowserImage(root, inside = false) {
    const config = JSON.parse(readFileSync(path.join(root, ".build/generated/plugin.json"), "utf8"));
    const manifestPath = path.join(root, config.browser?.directory || "tests/browser", "package.json");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    const require = createRequire(manifestPath);
    const declared = manifest.dependencies?.playwright || manifest.devDependencies?.playwright;
    const installed = require("playwright/package.json").version;
    verifyBrowserImage(declared, installed, config.images.playwright);
    if (inside) {
        const browsers = require("playwright");
        for (const name of ["chromium", "firefox", "webkit"])
            if (!existsSync(browsers[name].executablePath())) throw Error("Pinned image is missing " + name + " required executable.");
    }
    console.log("Verified Playwright " + declared + (inside ? " browser executables" : " image identity") + ".");
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const index = process.argv.indexOf("--root");
    checkBrowserImage(
        index < 0 ? process.env.JELLYFIN_PLUGIN_ROOT || process.cwd() : path.resolve(process.argv[index + 1]),
        process.argv.includes("--inside"),
    );
}
