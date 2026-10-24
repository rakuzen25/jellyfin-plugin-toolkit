// Consumers supply native imports so editors and dependency analyzers can see them.
export function createConfig({ js, defineConfig, prettier, globals, tseslint }, { typescriptFiles = ["**/*.ts"], ignores = [] } = {}) {
    return defineConfig(
        { ignores: ["dist/**", ".build/**", "**/node_modules/**", "vendor/**", "**/bin/**", "**/obj/**", "**/results*/**", ".venv/**", ...ignores] },
        {
            files: ["**/*.{mjs,cjs,ts}"],
            extends: [js.configs.recommended],
            languageOptions: { globals: { ...globals.browser, ...globals.node } },
            rules: { "no-template-curly-in-string": "error", "no-useless-rename": "error" },
        },
        ...(tseslint
            ? [
                  {
                      files: typescriptFiles,
                      extends: [tseslint.configs.recommended],
                      rules: {
                          "@typescript-eslint/consistent-type-imports": "error",
                          "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
                      },
                  },
              ]
            : []),
        prettier,
    );
}
