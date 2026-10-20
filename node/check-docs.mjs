import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { resolveDependency } from "./dependencies.mjs";

const directory = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(process.argv[2]);
const files = process.argv.slice(3);
if (files.length === 0) {
    console.log(JSON.stringify(["README.md: missing documentation entrypoint"]));
} else {
    const result = spawnSync(
        process.execPath,
        [
            resolveDependency("remark-cli", "cli.js"),
            "--no-config",
            "--no-ignore",
            "--no-stdout",
            "--frail",
            "--use",
            resolveDependency("remark-gfm"),
            "--use",
            resolveDependency("remark-lint"),
            "--use",
            resolveDependency("remark-lint-no-undefined-references") + "=allowShortcutLink:true",
            "--use",
            path.join(directory, "docs-policy.mjs"),
            "--use",
            resolveDependency("remark-validate-links") + "=repository:false",
            "--report",
            path.join(directory, "docs-report.mjs"),
            "--",
            ...files,
        ],
        { cwd: root, encoding: "utf8" },
    );
    if (result.error) throw result.error;
    if (result.status !== 0 && result.status !== 1) throw new Error(result.stderr || "remark failed");
    // remark writes reporters to stderr; treat unexpected diagnostics as a tool failure.
    const errors = JSON.parse(result.stderr);
    if (!Array.isArray(errors) || errors.some((error) => typeof error !== "string")) throw new Error("Invalid documentation report");
    console.log(JSON.stringify(errors));
}
