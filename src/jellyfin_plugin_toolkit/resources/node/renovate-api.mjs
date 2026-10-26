import fs from "node:fs";
// Public test adapter for the toolkit-owned, pinned Renovate runtime.
export { extractPackageFile } from "/usr/local/renovate/dist/modules/manager/custom/regex/index.js";
import { applyPackageRules } from "/usr/local/renovate/dist/util/package-rules/index.js";
export { applyPackageRules };
export { validateConfig } from "/usr/local/renovate/dist/config/validation.js";
import { getConfig } from "/usr/local/renovate/dist/config/defaults.js";
export { getConfig };
export { generateBranchConfig } from "/usr/local/renovate/dist/workers/repository/updates/generate.js";
export { compile } from "/usr/local/renovate/dist/util/template/index.js";
export { GlobalConfig } from "/usr/local/renovate/dist/config/global.js";
export { extractPackageFile as extractConfig } from "/usr/local/renovate/dist/modules/manager/renovate-config/index.js";
export { extractPackageFile as extractPython } from "/usr/local/renovate/dist/modules/manager/pep621/index.js";
export { extractPackageFile as extractActions } from "/usr/local/renovate/dist/modules/manager/github-actions/index.js";
export { extractPackageFile as extractPyenv } from "/usr/local/renovate/dist/modules/manager/pyenv/index.js";
export * as gitApi from "/usr/local/renovate/dist/util/git/index.js";

export function effectivePolicy(dependency, manager = "npm", packageFile = "package.json") {
    const local = JSON.parse(fs.readFileSync("renovate.json", "utf8"));
    const tooling = JSON.parse(fs.readFileSync(new URL("../tooling.json", import.meta.url), "utf8"));
    const plugin = local.extends.some((entry) => /^github>rakuzen25\/jellyfin-plugin-toolkit#/.test(entry))
        ? JSON.parse(fs.readFileSync(new URL("../default.json", import.meta.url), "utf8"))
        : {};
    return applyPackageRules({
        ...getConfig(),
        ...tooling,
        ...dependency,
        packageName: dependency.packageName ?? dependency.depName,
        manager,
        packageFile,
        packageRules: [...tooling.packageRules, ...(plugin.packageRules ?? []), ...(local.packageRules ?? [])],
    });
}
