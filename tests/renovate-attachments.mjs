import {
    extractPackageFile,
    applyPackageRules,
    validateConfig,
    extractConfig,
    getConfig,
    generateBranchConfig,
    compile as render,
    GlobalConfig,
    extractPython,
    extractActions,
    gitApi,
    extractPyenv,
} from "../.build/jpt/node/renovate-api.mjs";
// Run inside the pinned Renovate image. Exercise its shipped implementation.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const tooling = JSON.parse(fs.readFileSync(root + "/tooling.json", "utf8"));
assert.deepEqual(tooling, JSON.parse(fs.readFileSync(root + "/src/jellyfin_plugin_toolkit/resources/tooling.json", "utf8")));
assert.deepEqual((await validateConfig("tooling", tooling)).errors, []);
const preset = JSON.parse(fs.readFileSync(root + "/default.json", "utf8"));
assert.deepEqual(preset, JSON.parse(fs.readFileSync(root + "/src/jellyfin_plugin_toolkit/resources/default.json", "utf8")));
const validation = await validateConfig("preset", preset);
assert.deepEqual(validation.errors, []);
const policy = (dep, manager, packageFile, packageRules = [...tooling.packageRules, ...preset.packageRules]) =>
    applyPackageRules({
        enabled: true,
        minimumReleaseAge: "1 day",
        minimumReleaseAgeBuffer: "30 minutes",
        ...dep,
        packageName: dep.packageName ?? dep.depName,
        manager,
        packageFile,
        packageRules,
    });

const configured = await extractConfig(JSON.stringify({ extends: ["github>rakuzen25/jellyfin-plugin-toolkit#v0.2.0"] }), "renovate.json", {});
const toolkitPreset = configured.deps.find((dependency) => dependency.depName === "rakuzen25/jellyfin-plugin-toolkit");
assert.equal(toolkitPreset.currentValue, "v0.2.0");
assert.equal((await policy(toolkitPreset, "renovate-config", "renovate.json")).enabled, false);
const before = { tag_name: "v0.2.0" },
    after = { tag_name: "v0.3.0" };
const manifest =
    '[tool.uv.sources]\njellyfin-plugin-toolkit = { url = "https://github.com/rakuzen25/jellyfin-plugin-toolkit/releases/download/v0.2.0/jellyfin_plugin_toolkit-0.2.0-py3-none-any.whl" }\n';
const dependency = (await extractPackageFile(manifest, "pyproject.toml", preset.customManagers[0])).deps[0];
assert.equal(dependency.currentValue, before.tag_name);
assert.equal(dependency.datasource, "github-releases");
const wheelPolicy = await policy(dependency, "regex", "pyproject.toml");
assert.equal(wheelPolicy.enabled, true);
assert.equal(wheelPolicy.minimumReleaseAge, null);
assert.equal(wheelPolicy.minimumReleaseAgeBuffer, null);
const title = (selected, updateType = "patch", newVersion = "0.3.1") =>
    generateBranchConfig([
        {
            ...getConfig(),
            semanticCommits: "enabled",
            semanticCommitType: "chore",
            semanticCommitScope: "deps",
            ...selected,
            updateType,
            newVersion,
            newValue: "v" + newVersion,
            branchName: "renovate/jellyfin-plugin-toolkit",
        },
    ]).prTitle;
for (const [updateType, version] of [
    ["patch", "0.3.1"],
    ["minor", "0.4.0"],
    ["major", "1.0.0"],
]) {
    const selected = await policy({ ...dependency, updateType }, "regex", "pyproject.toml");
    assert.equal(title(selected, updateType, version), "chore(deps): update jellyfin plugin toolkit");
    if (updateType === "major") assert.equal(selected.dependencyDashboardApproval, true);
}
assert.equal(
    title(await policy({ depName: "example/library", datasource: "github-releases" }, "regex", "pyproject.toml")),
    "chore(deps): update dependency example/library to v0.3.1",
);
// A custom tag lookup or a URL outside pyproject.toml cannot select another version.
assert.equal((await policy({ ...dependency, datasource: "github-tags" }, "regex", "pyproject.toml")).enabled, false);
assert.equal((await policy(dependency, "regex", "other.toml")).enabled, false);

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
console.log("PASS: only the canonical wheel selects a published release, without a release-age delay");

// Reusable workflows need version comments: SHA-only references are disabled by Renovate.
GlobalConfig.set({ localDir: root });
const nativePython = await extractPython(
    '[project]\nname="fixture-tooling"\nversion="0.1.0"\nrequires-python=">=3.15,<3.16"\ndependencies=["jellyfin-plugin-toolkit"]\n' + manifest,
    "pyproject.toml",
    {},
);
assert.equal(nativePython.deps.find((dep) => dep.depName === "jellyfin-plugin-toolkit").skipReason, "unsupported-url");
console.log("PASS: native Python extraction leaves the GitHub wheel to the grouped release manager");

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
    assert.equal((await policy(dep, "github-actions", ".github/workflows/" + name + ".yml")).enabled, false);
}
const unrelatedAction = (
    await extractActions(
        "name: Fixture\non: push\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n",
        ".github/workflows/check.yml",
        {},
    )
).deps[0];
assert.equal((await policy(unrelatedAction, "github-actions", ".github/workflows/check.yml")).enabled, true);
assert.equal((await policy(unrelatedAction, "github-actions", ".github/workflows/check.yml")).minimumReleaseAge, "1 day");
const unrelatedPreset = (await extractConfig(JSON.stringify({ extends: ["github>example/preset#v1.0.0"] }), "renovate.json", {})).deps[0];
assert.equal((await policy(unrelatedPreset, "renovate-config", "renovate.json")).enabled, true);
console.log("PASS: independent toolkit tag updates are disabled; unrelated actions and presets remain enabled");

// Exercise consumer rules after the inherited preset: they must not re-enable tags
// or allow the toolkit to join routine automerge groups.
for (const name of ["jellyfin-lyricbridge", "jellyfin-plugin-music-metadata-manager"]) {
    const adoption = root + "/.build/released-0.4.0-adoption/" + name + "/renovate.json";
    const modern = root + "/.build/modernization/" + (name === "jellyfin-lyricbridge" ? "lyricbridge" : "music") + "/renovate.json";
    const filename = fs.existsSync(modern) ? modern : fs.existsSync(adoption) ? adoption : root + "/.build/native-rollout/" + name + "/renovate.json";
    if (!fs.existsSync(filename)) continue;
    const consumer = JSON.parse(fs.readFileSync(filename, "utf8"));
    if (fs.existsSync(modern)) assert.deepEqual((await validateConfig("consumer", consumer)).errors, []);
    const rules = [...tooling.packageRules, ...preset.packageRules, ...(consumer.packageRules ?? [])];
    for (const [depName, updateType, expected] of [
        ["prettier", "patch", true],
        ["prettier", "minor", true],
        ["node", "minor", false],
    ]) {
        const effective = await applyPackageRules({
            ...getConfig(),
            ...tooling,
            ...consumer,
            depName,
            packageName: depName,
            manager: "npm",
            packageFile: "package.json",
            updateType,
            packageRules: rules,
        });
        assert.equal(effective.automerge, expected, `${name}/${depName}/${updateType}`);
    }

    const wheel = await policy(dependency, "regex", "pyproject.toml", rules);
    assert.equal(wheel.enabled, true);
    assert.equal(wheel.automerge, false);
    assert.equal(wheel.minimumReleaseAge, null);
    assert.equal(wheel.minimumReleaseAgeBuffer, null);
    assert.equal(wheel.groupSlug, "jellyfin-plugin-toolkit");
    assert.equal(title(wheel), "chore(deps): update jellyfin plugin toolkit");
    assert.equal((await policy(toolkitPreset, "renovate-config", "renovate.json", rules)).enabled, false);
}
// Companion commits stay eligible for Renovate updates; human edits still stop them.
const { execFileSync } = await import("node:child_process");
const { tmpdir } = await import("node:os");
const temporary = fs.mkdtempSync(path.join(tmpdir(), "jpt-app-identity-"));
try {
    const source = path.join(temporary, "source"),
        checkout = path.join(temporary, "checkout");
    fs.mkdirSync(source);
    const git = (...args) => execFileSync("git", args, { cwd: source, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
    const commit = (name, email) => {
        git("-c", "user.name=" + name, "-c", "user.email=" + email, "commit", "--allow-empty", "-m", "fixture");
    };
    git("init", "-b", "main");
    commit("Fixture", "fixture@example.invalid");
    git("checkout", "-b", "renovate/toolkit");
    commit("Renovate", "renovate@example.invalid");
    const appEmail = JSON.parse(fs.readFileSync(root + "/renovate.json", "utf8")).gitIgnoredAuthors[0];
    commit("App bot", appEmail);
    git("checkout", "-b", "human-edited");
    commit("Human", "human@example.invalid");
    git("checkout", "main");
    execFileSync("git", ["clone", source, checkout], { stdio: "pipe" });
    GlobalConfig.set({ localDir: checkout, platform: "github" });
    await gitApi.initRepo({ url: source, currentBranch: "main", defaultBranch: "main", fullClone: true });
    gitApi.setUserRepoConfig({ gitAuthor: "Renovate <renovate@example.invalid>", gitIgnoredAuthors: [appEmail] });
    assert.equal(await gitApi.isBranchModified("renovate/toolkit", "main"), false);
    assert.equal(await gitApi.isBranchModified("human-edited", "main"), true);
    console.log("PASS: Renovate recognizes App companion commits and preserves human-edit protection");
} finally {
    fs.rmSync(temporary, { recursive: true, force: true });
}

// Verify shared policy independently of plugin-specific managers and exceptions.
for (const [depName, updateType, expected] of [
    ["prettier", "patch", true],
    ["prettier", "minor", true],
    ["prettier", "major", false],
    ["node", "patch", true],
    ["node", "minor", false],
    ["python", "minor", false],
    ["pnpm", "minor", false],
    ["uv", "minor", false],
    ["astral-sh/uv", "minor", false],
    ["typescript", "minor", false],
    ["ubuntu", "minor", false],
    ["actions/checkout", "digest", true],
]) {
    const selected = await policy({ depName, updateType }, "npm", "package.json", tooling.packageRules);
    assert.equal(selected.automerge, expected, `${depName}/${updateType}`);
    assert.equal(selected.minimumReleaseAge, "1 day");
}
const sharedPreset = await policy({ depName: "example/policy", updateType: "patch" }, "renovate-config", "renovate.json", tooling.packageRules);
assert.equal(sharedPreset.automerge, false);
const groupedTitle = generateBranchConfig(
    ["one", "two"].map((depName) => ({
        ...getConfig(),
        ...tooling,
        depName,
        packageName: depName,
        updateType: "patch",
        groupName: "Routine dependency updates",
        newVersion: "1.0.1",
        newValue: "1.0.1",
        branchName: "renovate/routine",
    })),
).prTitle;
assert.equal(groupedTitle, "chore(deps): update routine dependency updates to 1.0.1");
console.log("PASS: shared tooling policy keeps routine, runtime and policy updates separate");

// The executable and interpreter pins form one native Renovate group.
const uvProject = '[tool.uv]\nrequired-version = "==0.13.5"\npython-preference = "only-managed"\n';
assert.equal(tooling.customManagers.length, 1);
const uvDependencies = (await extractPackageFile(uvProject, "pyproject.toml", tooling.customManagers[0])).deps;
assert.equal(uvDependencies.length, 1);
const uvDependency = uvDependencies[0];
assert.equal(uvDependency.depName, "uv");
assert.equal(uvDependency.packageName, "astral-sh/uv");
assert.equal(uvDependency.currentValue, "0.13.5");
const uvReplacement = uvProject.replace(uvDependency.replaceString, uvDependency.replaceString.replace("0.13.5", "0.13.6"));
assert.equal((await extractPackageFile(uvReplacement, "pyproject.toml", tooling.customManagers[0])).deps[0].currentValue, "0.13.6");
const pythonDeps = (await extractPyenv("3.15.1\n", ".python-version", {})).deps;
assert.equal(pythonDeps.length, 1);
const pythonDependency = pythonDeps[0];
assert.equal(pythonDependency.currentValue, "3.15.1");
for (const [pythonType, uvType, expected] of [
    ["patch", "patch", true],
    ["patch", "minor", false],
    ["minor", "patch", false],
    ["major", "patch", false],
    ["patch", "major", false],
]) {
    const selected = await Promise.all(
        [
            [pythonDependency, "pyenv", ".python-version", pythonType],
            [uvDependency, "custom.regex", "pyproject.toml", uvType],
        ].map(async ([dep, manager, packageFile, updateType]) => ({
            ...getConfig(),
            ...tooling,
            ...(await policy({ ...dep, updateType }, manager, packageFile, tooling.packageRules)),
            updateType,
            newVersion: "4.0.0",
            newValue: "4.0.0",
            branchName: "renovate/python-toolchain",
        })),
    );
    assert.ok(selected.every((dep) => dep.groupName === "Python toolchain"));
    assert.ok(selected.every((dep) => dep.separateMajorMinor === false && dep.separateMinorPatch === false));
    // Test both orders: a patch must not overwrite a grouped migration's review requirement.
    for (const ordered of [selected, [...selected].reverse()]) {
        assert.equal(generateBranchConfig(ordered).automerge, expected, `${pythonType}/${uvType}`);
    }
}
const setupAction = await policy(
    { depName: "astral-sh/setup-uv", updateType: "patch" },
    "github-actions",
    ".github/workflows/ci.yml",
    tooling.packageRules,
);
assert.equal(setupAction.groupName, "Routine dependency updates");
const toolkitConfig = JSON.parse(fs.readFileSync(root + "/renovate.json", "utf8"));
assert.deepEqual(toolkitConfig.extends, ["github>rakuzen25/jellyfin-plugin-toolkit:tooling"]);
assert.equal(toolkitConfig.automerge, undefined);
assert.equal(toolkitConfig.packageRules, undefined);
console.log("PASS: interpreter and uv extraction, toolchain grouping, and mixed-group review policy");

// Only this repository tracks the exact shared validator runtimes.
const validatorPins = JSON.parse(fs.readFileSync(root + "/src/jellyfin_plugin_toolkit/resources/configs/validators.json", "utf8"));
const runtimeDependencies = (
    await extractPackageFile(
        JSON.stringify(validatorPins),
        "src/jellyfin_plugin_toolkit/resources/configs/validators.json",
        toolkitConfig.customManagers[0],
    )
).deps;
assert.equal(runtimeDependencies.length, 2);
for (const pin of Object.values(validatorPins)) {
    const [, name, version, digest] = pin.match(/^(.*):([^@]+)@(sha256:.*)$/);
    const dependency = runtimeDependencies.find((dep) => dep.depName === name);
    assert.equal(dependency.currentValue, version);
    assert.equal(dependency.currentDigest, digest);
}
console.log("PASS: one toolkit-owned source tracks both digest-pinned validator runtimes");
