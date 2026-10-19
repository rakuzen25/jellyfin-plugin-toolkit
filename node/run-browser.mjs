import { readFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { checkBrowserImage } from "./check-browser-image.mjs";
const root = path.resolve(process.argv[2]);
checkBrowserImage(root, true);
const config = JSON.parse(readFileSync(path.join(root, ".build/generated/plugin.json"), "utf8"));
const command = config.browser?.command || ["node", "node_modules/playwright/cli.js", "test"];
const result = spawnSync(command[0], [...command.slice(1), ...process.argv.slice(3)], {
    cwd: path.join(root, config.browser?.directory || "tests/browser"),
    stdio: "inherit",
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
