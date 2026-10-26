// Run inside the toolkit-owned Renovate runtime; repository assertions use the public adapter.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { effectivePolicy, extractPackageFile, extractPyenv, generateBranchConfig } from "./renovate-api.mjs";

const repository = JSON.parse(fs.readFileSync("renovate.json", "utf8"));
const tooling = JSON.parse(fs.readFileSync(new URL("../tooling.json", import.meta.url), "utf8"));
assert.ok(
    repository.extends.some((entry) => entry.startsWith("github>rakuzen25/jellyfin-plugin-toolkit:tooling")),
    "Repository must consume the common tooling preset",
);
const policy = (dependency, manager = "custom.regex", packageFile = "pyproject.toml") => effectivePolicy(dependency, manager, packageFile);
for (const [depName, updateType, automerge, groupName] of [
    ["prettier", "minor", true, "Routine dependency updates"],
    ["prettier", "major", false, null],
    ["python", "patch", true, "Python toolchain"],
    ["python", "minor", false, "Python toolchain"],
    ["uv", "patch", true, "Python toolchain"],
    ["uv", "minor", false, "Python toolchain"],
]) {
    const actual = await policy({ depName, updateType }, depName === "python" ? "pyenv" : "custom.regex");
    assert.equal(actual.automerge, automerge, `${depName}/${updateType}`);
    assert.equal(actual.groupName, groupName, `${depName}/${updateType}`);
}
for (const [python, uv, expected] of [
    ["patch", "patch", true],
    ["minor", "patch", false],
    ["patch", "minor", false],
    ["major", "patch", false],
    ["patch", "major", false],
]) {
    const selected = await Promise.all(
        [
            ["python", python],
            ["uv", uv],
        ].map(async ([depName, updateType]) => ({
            ...(await policy({ depName, updateType })),
            updateType,
            newVersion: "4.0.0",
            newValue: "4.0.0",
            branchName: "renovate/python-toolchain",
        })),
    );
    for (const ordered of [selected, [...selected].reverse()]) assert.equal(generateBranchConfig(ordered).automerge, expected);
}
const project = fs.readFileSync("pyproject.toml", "utf8");
const extracted = [];
for (const manager of [...tooling.customManagers, ...(repository.customManagers ?? [])]) {
    if (manager.managerFilePatterns.some((pattern) => pattern.includes("pyproject")))
        extracted.push(...((await extractPackageFile(project, "pyproject.toml", manager))?.deps ?? []));
}
assert.equal(extracted.filter((dep) => dep.depName === "uv").length, 1, "uv must be discovered exactly once");
assert.equal((await extractPyenv(fs.readFileSync(".python-version", "utf8"), ".python-version", {})).deps.length, 1);
for (const rule of repository.packageRules ?? []) {
    if (rule.automerge !== false || !rule.matchPackageNames || !rule.matchUpdateTypes || rule.matchPackageNames.some((name) => name.includes("*")))
        continue;
    for (const depName of rule.matchPackageNames)
        for (const updateType of rule.matchUpdateTypes) assert.equal((await policy({ depName, updateType }, "npm", "package.json")).automerge, false);
}
console.log("PASS: effective dependency grouping, single toolchain extraction and automerge review requirements");
if (process.argv[2]) await import(pathToFileURL(path.resolve(process.argv[2])).href);
