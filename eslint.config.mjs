import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import prettier from "eslint-config-prettier";
import globals from "globals";
import tseslint from "typescript-eslint";
import { createConfig } from "./configs/eslint.mjs";
export default createConfig({ js, defineConfig, prettier, globals, tseslint });
