// Run inside the pinned Renovate image. Exercise its shipped implementation.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { extractPackageFile } from "/usr/local/renovate/dist/modules/manager/custom/regex/index.js";
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const { applyPackageRules } = await import("/usr/local/renovate/dist/util/package-rules/index.js");
const { validateConfig } = await import("/usr/local/renovate/dist/config/validation.js");
const preset = JSON.parse(fs.readFileSync(root + "/default.json", "utf8"));
assert.deepEqual(preset, JSON.parse(fs.readFileSync(root + "/src/jellyfin_plugin_toolkit/resources/default.json", "utf8")));
const validation = await validateConfig("preset", preset);
assert.deepEqual(validation.errors, []);
const policy = (dep, manager, packageFile, packageRules = preset.packageRules) =>
    applyPackageRules({ enabled: true, ...dep, packageName: dep.packageName ?? dep.depName, manager, packageFile, packageRules });

const { extractPackageFile: extractConfig } = await import("/usr/local/renovate/dist/modules/manager/renovate-config/index.js");
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
assert.equal(wheelPolicy.minimumReleaseAge, "1 day");
assert.equal(wheelPolicy.minimumReleaseAgeBehaviour, "timestamp-required");
// A custom tag lookup or a URL outside pyproject.toml cannot select another version.
assert.equal((await policy({ ...dependency, datasource: "github-tags" }, "regex", "pyproject.toml")).enabled, false);
assert.equal((await policy(dependency, "regex", "other.toml")).enabled, false);

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
console.log("PASS: only the canonical wheel selects a published release, with the 24-hour cutoff");

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
const unrelatedPreset = (await extractConfig(JSON.stringify({ extends: ["github>example/preset#v1.0.0"] }), "renovate.json", {})).deps[0];
assert.equal((await policy(unrelatedPreset, "renovate-config", "renovate.json")).enabled, true);
console.log("PASS: independent toolkit tag updates are disabled; unrelated actions and presets remain enabled");

// Exercise consumer rules after the inherited preset: they must not re-enable tags
// or allow the toolkit to join routine automerge groups.
for (const name of ["jellyfin-lyricbridge", "jellyfin-plugin-music-metadata-manager"]) {
    const filename = root + "/.build/native-rollout/" + name + "/renovate.json";
    if (!fs.existsSync(filename)) continue;
    const consumer = JSON.parse(fs.readFileSync(filename, "utf8"));
    const rules = [...preset.packageRules, ...(consumer.packageRules ?? [])];
    const wheel = await policy(dependency, "regex", "pyproject.toml", rules);
    assert.equal(wheel.enabled, true);
    assert.equal(wheel.automerge, false);
    assert.equal(wheel.groupSlug, "jellyfin-plugin-toolkit");
    assert.equal((await policy(toolkitPreset, "renovate-config", "renovate.json", rules)).enabled, false);
}
// Companion commits stay eligible for Renovate updates; human edits still stop them.
const { execFileSync } = await import("node:child_process");
const { tmpdir } = await import("node:os");
const gitApi = await import("/usr/local/renovate/dist/util/git/index.js");
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
