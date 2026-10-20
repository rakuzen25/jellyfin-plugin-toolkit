import assert from "node:assert/strict";
import { cp, mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const toolkit = fileURLToPath(new URL("../", import.meta.url));
test("native consumer configuration discovers shared presets and retains overrides", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "toolkit-presets-"));
    try {
        await cp(path.join(toolkit, "configs"), path.join(root, ".build/toolkit/current/configs"), { recursive: true });
        await symlink(path.join(toolkit, "node_modules"), path.join(root, "node_modules"), "dir");
        await writeFile(path.join(root, "package.json"), '{"private":true}');
        await writeFile(
            path.join(root, "eslint.config.mjs"),
            'import js from "@eslint/js"; import {defineConfig} from "eslint/config"; import prettier from "eslint-config-prettier"; import globals from "globals"; import tseslint from "typescript-eslint"; import {createConfig} from "./.build/toolkit/current/configs/eslint.mjs"; export default [...createConfig({js,defineConfig,prettier,globals,tseslint}),{files:["app/**/*.ts"],rules:{"@typescript-eslint/no-unused-vars":"off"}}];',
        );
        await writeFile(
            path.join(root, ".prettierrc.mjs"),
            'import defaults from "./.build/toolkit/current/configs/prettier.mjs"; export default {...defaults,printWidth:88};',
        );
        await mkdir(path.join(root, "app"));
        await writeFile(
            path.join(root, "app/tsconfig.json"),
            '{"extends":"../.build/toolkit/current/configs/tsconfig.json","compilerOptions":{"strict":false},"include":["**/*.ts"]}',
        );
        await writeFile(path.join(root, "app/example.ts"), "export const answer = 42;\n");
        const require = createRequire(path.join(root, "package.json"));
        const { ESLint } = require("eslint");
        const linter = new ESLint({ cwd: root });
        const config = await linter.calculateConfigForFile(path.join(root, "app/example.ts"));
        assert.equal(config.rules["no-template-curly-in-string"][0], 2);
        assert.equal(config.rules["@typescript-eslint/no-unused-vars"][0], 0);
        const prettier = require("prettier");
        const formatting = await prettier.resolveConfig(path.join(root, "app/example.ts"));
        assert.equal(formatting.printWidth, 88);
        assert.equal(formatting.tabWidth, 4);
        const ts = require("typescript");
        const file = ts.readConfigFile(path.join(root, "app/tsconfig.json"), ts.sys.readFile);
        const parsed = ts.parseJsonConfigFileContent(file.config, ts.sys, path.join(root, "app"));
        assert.equal(parsed.options.strict, false);
        assert.equal(parsed.options.noUncheckedIndexedAccess, true);
        assert.deepEqual(parsed.fileNames, [path.join(root, "app/example.ts")]);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});
