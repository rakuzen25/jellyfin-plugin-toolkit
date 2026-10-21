// Run inside the pinned Renovate image. Exercise its shipped implementation.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { extractPackageFile } from "/usr/local/renovate/dist/modules/manager/custom/regex/index.js";
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const preset = JSON.parse(fs.readFileSync(root + "/default.json", "utf8"));
assert.deepEqual(preset, JSON.parse(fs.readFileSync(root + "/src/jellyfin_plugin_toolkit/resources/default.json", "utf8")));
const { extractPackageFile: extractConfig } = await import("/usr/local/renovate/dist/modules/manager/renovate-config/index.js");
const configured = await extractConfig(JSON.stringify({ extends: ["github>rakuzen25/jellyfin-plugin-toolkit#v0.2.0"] }), "renovate.json", {});
assert.ok(configured.deps.some((dependency) => dependency.currentValue === "v0.2.0" && dependency.depName === "rakuzen25/jellyfin-plugin-toolkit"));
const before = { tag_name: "v0.2.0" },
    after = { tag_name: "v0.3.0" };
const manifest =
    '[tool.uv.sources]\njellyfin-plugin-toolkit = { url = "https://github.com/rakuzen25/jellyfin-plugin-toolkit/releases/download/v0.2.0/jellyfin_plugin_toolkit-0.2.0-py3-none-any.whl" }\n';
const dependency = (await extractPackageFile(manifest, "pyproject.toml", preset.customManagers[0])).deps[0];
assert.equal(dependency.currentValue, before.tag_name);
assert.equal(dependency.datasource, "github-releases");
const { compile: render } = await import("/usr/local/renovate/dist/util/template/index.js");
const replacement = render(preset.customManagers[0].autoReplaceStringTemplate, { ...dependency, newValue: after.tag_name });
const updated = manifest.replace(dependency.replaceString, replacement);
const next = (await extractPackageFile(updated, "pyproject.toml", preset.customManagers[0])).deps[0];
assert.equal(next.currentValue, after.tag_name);
assert.ok(updated.includes("/v0.3.0/jellyfin_plugin_toolkit-0.3.0-py3-none-any.whl"));
const rules = preset.packageRules.filter((rule) => rule.groupName);
assert.equal(rules.length, 1);
assert.equal(rules[0].automerge, false);
assert.ok(rules[0].matchPackageNames.includes("rakuzen25/jellyfin-plugin-toolkit/**"));
assert.ok(preset.packageRules.some((rule) => rule.matchUpdateTypes?.includes("major") && rule.dependencyDashboardApproval));
console.log("PASS: native Renovate extraction and replacement coordinate canonical wheel URL and preset updates");

// Reusable workflows need version comments: SHA-only references are disabled by Renovate.
const { GlobalConfig } = await import("/usr/local/renovate/dist/config/global.js");
GlobalConfig.set({ localDir: root });
const { extractPackageFile: extractPython } = await import("/usr/local/renovate/dist/modules/manager/pep621/index.js");
const nativePython = await extractPython(
    '[project]\nname="fixture-tooling"\nversion="0.1.0"\nrequires-python=">=3.15,<3.16"\ndependencies=["jellyfin-plugin-toolkit"]\n' + manifest,
    "pyproject.toml",
    {},
);
assert.equal(nativePython.deps.find((dep) => dep.depName === "jellyfin-plugin-toolkit").skipReason, "unsupported-url");
console.log("PASS: native Python extraction leaves the GitHub wheel to the grouped release manager");

const { extractPackageFile: extractActions } = await import("/usr/local/renovate/dist/modules/manager/github-actions/index.js");
const { compile } = await import("/usr/local/renovate/dist/util/template/index.js");
for (const name of ["validate", "release", "prepare-release", "catalog-notify"]) {
    const workflow =
        "name: Fixture\non: push\njobs:\n  call:\n    uses: rakuzen25/jellyfin-plugin-toolkit/.github/workflows/" +
        name +
        ".yml@" +
        "a".repeat(40) +
        " # " +
        before.tag_name +
        "\n";
    const dep = (await extractActions(workflow, ".github/workflows/" + name + ".yml", {})).deps[0];
    assert.notEqual(dep.enabled, false);
    assert.equal(dep.currentValue, before.tag_name);
    assert.equal(dep.currentDigest, "a".repeat(40));
    const replacement = compile(dep.autoReplaceStringTemplate, { ...dep, newValue: after.tag_name, newDigest: "b".repeat(40) });
    const updated = workflow.replace(dep.replaceString, replacement);
    const next = (await extractActions(updated, ".github/workflows/" + name + ".yml", {})).deps[0];
    assert.equal(next.currentValue, after.tag_name);
    assert.equal(next.currentDigest, "b".repeat(40));
    assert.equal(next.depName, dependency.depName);
}
console.log(
    "PASS: native GitHub Actions extraction and replacement update version comments and all reusable workflow digests with the toolkit release",
);
