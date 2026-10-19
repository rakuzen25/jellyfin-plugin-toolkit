// @ts-check
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import prettierConfig from "eslint-config-prettier";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig(
    { ignores: ["dist/**", ".build/**", "**/node_modules/**", "vendor/**", "**/bin/**", "**/obj/**", "**/results*/**", ".venv/**"] },
    {
        files: ["**/*.{mjs,cjs,ts}"],
        extends: [js.configs.recommended],
        languageOptions: { globals: { ...globals.browser, ...globals.node } },
        rules: { "no-template-curly-in-string": "error", "no-useless-rename": "error" },
    },
    {
        files: ["**/*.ts"],
        extends: [tseslint.configs.recommended],
        rules: {
            "@typescript-eslint/consistent-type-imports": "error",
            "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
        },
    },
    prettierConfig,
);
