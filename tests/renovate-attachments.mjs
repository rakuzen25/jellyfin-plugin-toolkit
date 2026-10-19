// Run inside the pinned Renovate image. Exercise its shipped implementation.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { GithubReleaseAttachmentsDatasource } from "/usr/local/renovate/dist/modules/datasource/github-release-attachments/index.js";
import { extractPackageFile } from "/usr/local/renovate/dist/modules/manager/custom/regex/index.js";
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const preset = JSON.parse(fs.readFileSync(root + "/default.json", "utf8"));
const { extractPackageFile: extractConfig } = await import("/usr/local/renovate/dist/modules/manager/renovate-config/index.js");
const configured = await extractConfig(JSON.stringify({ extends: ["github>rakuzen25/jellyfin-plugin-toolkit#v1.0.0"] }), "renovate.json", {});
assert.ok(configured.deps.some((dependency) => dependency.currentValue === "v1.0.0" && dependency.depName === "rakuzen25/jellyfin-plugin-toolkit"));
const before = JSON.parse(fs.readFileSync(root + "/tests/fixtures/releases/1.0.0.json", "utf8"));
const after = JSON.parse(fs.readFileSync(root + "/tests/fixtures/releases/1.0.1.json", "utf8"));
const releases = [before, after];
for (const release of releases)
    for (const asset of release.assets) {
        asset.size = asset.body.length;
        asset.browser_download_url = "https://github.com/example/toolkit/releases/download/" + release.tag_name + "/" + asset.name;
    }
const currentDigest = before.assets[0].body.split(" ")[0],
    expectedDigest = after.assets[0].body.split(" ")[0];
const lock = JSON.stringify({ schema: 1, repository: "rakuzen25/jellyfin-plugin-toolkit", version: before.tag_name, sha256: currentDigest }, null, 4);
const extracted = await extractPackageFile(lock, "toolkit.lock.json", preset.customManagers[0]);
assert.equal(extracted.deps.length, 1);
const dependency = extracted.deps[0];
assert.equal(dependency.currentValue, before.tag_name);
assert.equal(dependency.currentDigest, currentDigest);
assert.equal(dependency.datasource, "github-release-attachments");
const datasource = new GithubReleaseAttachmentsDatasource();
datasource.http = {
    getJsonUnchecked: async (url) => {
        const tag = url.split("/").at(-1);
        const body = releases.find((release) => release.tag_name === tag);
        assert.ok(body, "unexpected release request: " + url);
        return { body };
    },
    getText: async (url) => {
        const asset = releases.flatMap((release) => release.assets).find((asset) => asset.browser_download_url === url);
        assert.ok(asset, "unexpected attachment request: " + url);
        return { body: asset.body };
    },
};
const digest = await datasource.getDigest(
    { packageName: dependency.depName, currentValue: dependency.currentValue, currentDigest: dependency.currentDigest },
    after.tag_name,
);
assert.equal(digest, expectedDigest);
const updated = lock.replace(before.tag_name, after.tag_name).replace(currentDigest, digest);
const next = (await extractPackageFile(updated, "toolkit.lock.json", preset.customManagers[0])).deps[0];
assert.equal(next.currentValue, after.tag_name);
assert.equal(next.currentDigest, expectedDigest);
const rules = preset.packageRules.filter((rule) => rule.groupName);
assert.equal(rules.length, 1);
assert.equal(rules[0].automerge, false);
assert.equal(rules[0].pinDigests, true);
assert.ok(rules[0].matchPackageNames.includes("rakuzen25/jellyfin-plugin-toolkit/**"));
assert.ok(preset.packageRules.some((rule) => rule.matchUpdateTypes?.includes("major") && rule.dependencyDashboardApproval));
console.log(
    "PASS: Renovate extracts the lock and updates its release/checksum using consecutive release attachments; workflow pins share one manually reviewed group",
);
