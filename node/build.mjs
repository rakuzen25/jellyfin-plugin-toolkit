import { createRequire } from "node:module";
import { readFile, readdir, writeFile, mkdir, rm, rename, mkdtemp } from "node:fs/promises";
import path from "node:path";
import { execFileSync } from "node:child_process";

export async function createBuilder(root) {
    const configuration = JSON.parse(await readFile(path.join(root, ".build/generated/plugin.json"), "utf8"));
    const frontend = configuration.frontend;
    const directory = frontend.directory || "frontend";
    const { build } = createRequire(path.join(root, directory, "package.json"))("esbuild");
    const bundleEntries = frontend.entries || {};
    const rendererAssets = frontend.reload_assets || [];
    async function compileWeb(sourceRoot = root) {
        const options = {
            absWorkingDir: path.join(sourceRoot, directory),
            bundle: true,
            define: { [frontend.version_symbol || "__PLUGIN_VERSION__"]: JSON.stringify(configuration.version) },
            format: "iife",
            target: ["es2022"],
            legalComments: "inline",
            write: false,
            logLevel: "silent",
        };
        const files = new Map();
        const assets = path.join(sourceRoot, frontend.assets || directory + "/assets");
        try {
            for (const entry of await readdir(assets, { recursive: true, withFileTypes: true })) {
                if (entry.isFile()) {
                    const absolute = path.join(entry.parentPath, entry.name);
                    files.set(path.relative(assets, absolute), await readFile(absolute));
                }
            }
        } catch (error) {
            if (error.code !== "ENOENT") throw error;
        }
        for (const entry of frontend.extra_assets || []) files.set(entry.name, await readFile(path.join(sourceRoot, entry.source)));
        for (const [entry, output] of Object.entries(bundleEntries)) {
            const result = await build({
                ...options,
                entryPoints: [entry],
                banner: { js: frontend.banners?.[entry] || "/* Generated from maintained TypeScript sources. */" },
            });
            files.set(output, result.outputFiles[0].contents);
        }
        if (frontend.admin_entry) {
            const result = await build({ ...options, supported: { "template-literal": false }, entryPoints: [frontend.admin_entry] });
            const script = result.outputFiles[0].text;
            if (script.includes("${")) throw new Error("Inline administrator script contains Jellyfin localization tokens");
            let template = await readFile(path.join(sourceRoot, frontend.admin_template), "utf8");
            const marker = frontend.script_marker || "<!-- PLUGIN_CONFIG_SCRIPT -->";
            if (!template.includes(marker)) throw new Error("Missing administrator script marker");
            if (frontend.admin_styles)
                template = template.replace(
                    frontend.style_marker || "/* PLUGIN_STYLES */",
                    await readFile(path.join(sourceRoot, frontend.admin_styles), "utf8"),
                );
            files.set(
                "config.html",
                Buffer.from(template.replace(marker, "<script>\n" + script.replaceAll("</script", "<\\/script") + "\n</script>")),
            );
        }
        return files;
    }
    async function writeGeneration(files, directory) {
        await mkdir(directory, { recursive: true });
        for (const [name, contents] of files) {
            const target = path.join(directory, name);
            if (!target.startsWith(path.resolve(directory) + path.sep)) throw new Error("Asset output escapes destination");
            await mkdir(path.dirname(target), { recursive: true });
            await writeFile(target, contents);
        }
    }
    async function buildWeb(output = path.join(root, "dist/web"), sourceRoot = root) {
        const files = await compileWeb(sourceRoot);
        await mkdir(path.dirname(output), { recursive: true });
        const staged = await mkdtemp(output + "-");
        const previous = staged + "-previous";
        let saved = false,
            activated = false;
        try {
            await writeGeneration(files, staged);
            try {
                await rename(output, previous);
                saved = true;
            } catch (error) {
                if (error.code !== "ENOENT") throw error;
            }
            try {
                await rename(staged, output);
                activated = true;
            } catch (error) {
                if (saved) await rename(previous, output);
                throw error;
            }
        } finally {
            await rm(staged, { recursive: true, force: true });
            if (activated) await rm(previous, { recursive: true, force: true });
        }
    }
    async function main() {
        if (frontend.boundary_check) execFileSync(process.execPath, [frontend.boundary_check], { cwd: root, stdio: "inherit" });
        execFileSync(process.env.JELLYFIN_PLUGIN_PNPM || "pnpm", ["--dir", directory, "exec", "tsc", "--noEmit"], { cwd: root, stdio: "inherit" });
        await buildWeb();
    }
    return { root, bundleEntries, rendererAssets, compileWeb, writeGeneration, buildWeb, main };
}
