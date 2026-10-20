import assert from "node:assert/strict";
import { test } from "node:test";
import { verifyBrowserImage } from "../node/check-browser-image.mjs";

const image = `mcr.microsoft.com/playwright:v1.64.0-noble@sha256:${"a".repeat(64)}`;
test("browser package, installed version, distribution and immutable image must agree", () => {
    verifyBrowserImage("1.64.0", "1.64.0", image);
    assert.throws(() => verifyBrowserImage("1.65.0", "1.65.0", image), /identical versions/);
    assert.throws(() => verifyBrowserImage("1.64.0", "1.58.2", image), /identical versions/);
    assert.throws(() => verifyBrowserImage("1.64.0", "1.64.0", image.replace("noble", "jammy")), /identical versions/);
    assert.throws(() => verifyBrowserImage("1.64.0", "1.64.0", image.split("@")[0]), /identical versions/);
});
