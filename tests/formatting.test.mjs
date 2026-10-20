import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const root = fileURLToPath(new URL("../", import.meta.url));
const execute = promisify(execFile);

test("generated root changelog is excluded from formatting while maintained Markdown remains covered", async () => {
    const { getFileInfo } = await import("prettier");
    const options = { ignorePath: path.join(root, ".prettierignore") };
    const changelog = path.join(root, "CHANGELOG.md");
    assert.equal((await getFileInfo(changelog, options)).ignored, true);
    for (const file of ["README.md", "AGENTS.md", "docs/how-to/validate.md", "docs/CHANGELOG.md"]) {
        assert.equal((await getFileInfo(path.join(root, file), options)).ignored, false, file);
    }
    const before = await readFile(changelog, "utf8");
    await execute(path.join(root, "node_modules/.bin/prettier"), ["--check", changelog], { cwd: root });
    assert.equal(await readFile(changelog, "utf8"), before);
});

test("Markdown and TOML checks reject bad layout, repair it, and remain idempotent", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "toolkit-format-"));
    try {
        for (const [name, input, binary, command] of [
            ["guide.md", "# Guide\n- first\n- second\n", "prettier", []],
            ["project.toml", 'name="fixture"\nvalues=[1,2,3]\n', "taplo", ["fmt"]],
        ]) {
            const file = path.join(directory, name);
            const tool = path.join(root, "node_modules/.bin", binary);
            await writeFile(file, input);
            await assert.rejects(execute(tool, [...command, "--check", file]));
            assert.equal(await readFile(file, "utf8"), input);
            const format = [...command, ...(binary === "prettier" ? ["--write"] : []), file];
            await execute(tool, format);
            const formatted = await readFile(file, "utf8");
            await execute(tool, [...command, "--check", file]);
            await execute(tool, format);
            assert.equal(await readFile(file, "utf8"), formatted);
        }
        const invalid = path.join(directory, "invalid.toml");
        await writeFile(invalid, "[broken\n");
        await assert.rejects(execute(path.join(root, "node_modules/.bin/taplo"), ["fmt", "--check", invalid]));
    } finally {
        await rm(directory, { recursive: true, force: true });
    }
});
