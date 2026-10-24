import assert from "node:assert/strict";
import test from "node:test";
import js from "@eslint/js";
import { ESLint } from "eslint";
import { defineConfig } from "eslint/config";
import prettier from "eslint-config-prettier";
import globals from "globals";
import { createConfig } from "../src/jellyfin_plugin_toolkit/resources/configs/eslint.mjs";

test("JavaScript-only consumers enforce shared lint rules without TypeScript tooling", async () => {
    const eslint = new ESLint({ overrideConfigFile: true, overrideConfig: createConfig({ js, defineConfig, prettier, globals }) });
    const [invalid] = await eslint.lintText("console.log(missingIdentifier);\n", { filePath: "fixture.mjs" });
    assert.ok(invalid.messages.some((message) => message.ruleId === "no-undef"));
    const [valid] = await eslint.lintText("console.log('valid');\n", { filePath: "fixture.mjs" });
    assert.equal(valid.errorCount, 0);
});
