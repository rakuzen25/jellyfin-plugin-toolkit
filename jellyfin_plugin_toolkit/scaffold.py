"""Generate small consumers of the foundation without copying its implementation."""

import hashlib
import json
import re
import shutil
import uuid
from datetime import date
from pathlib import Path

from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT, relative


def value(v):
    if isinstance(v, dict):
        return (
            "{ "
            + ", ".join(json.dumps(str(k)) + " = " + value(w) for k, w in v.items())
            + " }"
        )
    if isinstance(v, list):
        return "[" + ", ".join(value(w) for w in v) + "]"
    return json.dumps(v, ensure_ascii=False)


def toml(config):
    return "".join(
        (
            "["
            + name
            + "]\n"
            + "".join(
                json.dumps(k) + " = " + value(v) + "\n" for k, v in values.items()
            )
            + "\n"
        )
        if isinstance(values, dict)
        else name + " = " + value(values) + "\n\n"
        for name, values in config.items()
    )


def generate(
    context,
    destination,
    identifier,
    name,
    repository,
    admin=False,
    host_project="src/Host/Plugin.csproj",
    *,
    toolkit_archive=None,
    sync_workflow=False,
):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", identifier):
        raise ValueError("Invalid plugin id")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid repository identity")
    if destination.exists():
        raise ValueError("Starter destination already exists")
    relative(destination, host_project)
    defaults = json.loads((TOOLKIT_ROOT / "templates/defaults.json").read_text())
    if context is not None:
        lock = json.loads((context.root / "toolkit.lock.json").read_text())
    else:
        from jellyfin_plugin_toolkit.archive import pack

        archive, digest = (
            pack(TOOLKIT_ROOT, TOOLKIT_ROOT / "dist")
            if toolkit_archive is None
            else (
                toolkit_archive,
                hashlib.sha256(toolkit_archive.read_bytes()).hexdigest(),
            )
        )
        lock = {
            "schema": 1,
            "repository": "rakuzen25/jellyfin-plugin-toolkit",
            "version": "v" + (TOOLKIT_ROOT / "version.txt").read_text().strip(),
            "sha256": digest,
        }
    assembly = "Jellyfin.Plugin." + "".join(
        p.capitalize() for p in identifier.split("-")
    )
    source_root = Path(host_project).parts[0]
    groups = ["metadata", "backend", "tooling", "artifacts"] + (
        ["frontend", "workflow"] if admin else []
    )
    test = "tests/backend/Host/Plugin.Tests.csproj"
    config = {
        "schema": 1,
        "plugin": {
            "id": identifier,
            "name": name,
            "guid": str(uuid.uuid4()),
            "assembly": assembly,
            "repository": repository,
            "owner": repository.split("/")[0],
            "description": name + " for Jellyfin",
            "overview": name + " plugin",
            "category": "General",
        },
        "host": defaults["host"],
        "images": defaults["images"],
        "backend": {
            "entry": host_project,
            "projects": {"Host": host_project},
            "project_assemblies": [assembly],
            "tests": [test],
            "source_roots": [source_root, "tests/backend"],
        },
        "architecture": {
            "edges": {host_project: [], test: [host_project]},
            "forbidden": {},
        },
        "frontend": {"enabled": admin},
        "source": {
            "roots": list(
                dict.fromkeys(
                    [source_root, "tests", "scripts", "docs", ".github"]
                    + (["frontend"] if admin else [])
                )
            )
        },
        "validation": {
            "groups": groups,
            "check_targets": ["backend", "tooling", "docs"]
            + (["frontend"] if admin else []),
            "unit_groups": ["backend"],
            "acceptance_groups": ["installation", "reproducibility"],
            "rules": [
                {"name": "docs", "patterns": ["*.md", "docs/**"], "groups": []},
                {
                    "name": "maintained",
                    "patterns": [
                        source_root + "/**",
                        "tests/**",
                        "scripts/**",
                        "frontend/**",
                        ".github/**",
                        "plugin",
                        "plugin.toml",
                        "toolkit.lock.json",
                        "version.txt",
                        "Directory.Build.props",
                        "global.json",
                        "package.json",
                        "pnpm-*.yaml",
                        "*.mjs",
                        "*.toml",
                        ".*",
                        "LICENSE",
                        "NOTICE",
                        "GPL-3.0.txt",
                    ],
                    "groups": groups,
                },
            ],
        },
        "distribution": {
            "web": False,
            "notices": [{"source": n} for n in ["LICENSE", "NOTICE", "GPL-3.0.txt"]],
            "licenses": {
                assembly + ".dll": {
                    "license": "Apache-2.0",
                    "notices": ["LICENSE", "GPL-3.0.txt"],
                }
            },
        },
    }
    if admin:
        config["frontend"].update(
            {
                "directory": "frontend",
                "entries": {},
                "admin_entry": "admin/config.ts",
                "admin_template": "frontend/admin/config.html",
                "script_marker": "<!-- PLUGIN_CONFIG_SCRIPT -->",
                "required_assets": ["config.html"],
            }
        )
        config["sandbox"] = {
            "settings_page": identifier,
            "asset_prefix": "/__no_plugin_assets__/",
        }
    destination.mkdir(parents=True)

    def put(name, text):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    put("plugin.toml", toml(config))
    put("toolkit.lock.json", json.dumps(lock, indent=4) + "\n")
    put("version.txt", "0.1.0\n")
    put(
        "renovate.json",
        json.dumps(
            {
                "$schema": "https://docs.renovatebot.com/renovate-schema.json",
                "extends": [
                    "config:recommended",
                    "github>" + lock["repository"] + "#" + lock["version"],
                ],
                "automerge": False,
                "minimumReleaseAge": "1 day",
            },
            indent=4,
        )
        + "\n",
    )
    from jellyfin_plugin_toolkit.bindings import DESCRIPTOR

    shutil.copy2(TOOLKIT_ROOT / DESCRIPTOR["launcher"], destination / "plugin")
    (destination / "plugin").chmod(0o755)
    for pin, contents in defaults["pins"].items():
        put(pin, contents)
    for notice in ["LICENSE", "GPL-3.0.txt"]:
        shutil.copy2(TOOLKIT_ROOT / notice, destination / notice)
    put(
        "NOTICE",
        "Original "
        + name
        + " code is Apache-2.0. Compiled Jellyfin plugin distributions also comply with GPLv3.\n",
    )
    put(
        ".gitignore",
        ".build/\ndist/\n.venv/\nnode_modules/\n/build-support/toolkit.tar.gz\n**/bin/\n**/obj/\n**/__pycache__/\n**/results-run.*/\n**/TestResults/\n.ruff_cache/\n.mypy_cache/\n",
    )
    put(
        "Directory.Build.props",
        '<Project>\n  <PropertyGroup><PluginRepositoryRoot>$(MSBuildThisFileDirectory)</PluginRepositoryRoot></PropertyGroup>\n  <Import Project=".build/toolkit/current/msbuild/Plugin.props" />\n</Project>\n',
    )
    embedded = (
        '<EmbeddedResource Include="$(PluginWebAssets)/config.html" Link="Web/config.html"/>'
        if admin
        else ""
    )
    put(
        host_project,
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><AssemblyName>'
        + assembly
        + "</AssemblyName><RootNamespace>"
        + assembly
        + '</RootNamespace></PropertyGroup><ItemGroup><Reference Include="$(JellyfinReferenceDir)/MediaBrowser.*.dll" Private="false"/><FrameworkReference Include="Microsoft.AspNetCore.App"/>'
        + embedded
        + "</ItemGroup></Project>\n",
    )
    page = ", IHasWebPages" if admin else ""
    pages = (
        "public IEnumerable<PluginPageInfo> GetPages() => [new() { Name = "
        + json.dumps(identifier)
        + ", EmbeddedResourcePath = "
        + json.dumps(assembly + ".Web.config.html")
        + " }];"
        if admin
        else ""
    )
    put(
        str(Path(host_project).parent / "Plugin.cs"),
        "using MediaBrowser.Common.Configuration;\nusing MediaBrowser.Common.Plugins;\nusing MediaBrowser.Model.Plugins;\nusing MediaBrowser.Model.Serialization;\nnamespace ExamplePlugin;\npublic sealed class Configuration : BasePluginConfiguration {}\npublic sealed class Plugin(IApplicationPaths paths, IXmlSerializer serializer) : BasePlugin<Configuration>(paths, serializer)"
        + page
        + "\n{\npublic override string Name => "
        + json.dumps(name)
        + ";\npublic override Guid Id => Guid.Parse("
        + json.dumps(config["plugin"]["guid"])
        + ");\n"
        + pages
        + "\n}\n",
    )
    relative_project = __import__("os").path.relpath(
        destination / host_project, destination / Path(test).parent
    )
    put(
        test,
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><IsTestProject>true</IsTestProject><OutputType>Exe</OutputType><UseMicrosoftTestingPlatformRunner>true</UseMicrosoftTestingPlatformRunner></PropertyGroup><ItemGroup><ProjectReference Include="'
        + relative_project
        + '"/><PackageReference Include="xunit.v3" Version="4.0.2" PrivateAssets="all"/><Reference Include="$(JellyfinReferenceDir)/MediaBrowser.*.dll"/></ItemGroup></Project>\n',
    )
    put(
        str(Path(host_project).parent / "packages.lock.json"),
        json.dumps({"version": 1, "dependencies": {"net10.0": {}}}, indent=2) + "\n",
    )
    test_lock = json.loads(
        (TOOLKIT_ROOT / "templates/test-host.packages.lock.json").read_text()
    )
    for dependencies in test_lock["dependencies"].values():
        dependencies[assembly.lower()] = dependencies.pop("HOST_ASSEMBLY")
    put(
        str(Path(test).parent / "packages.lock.json"),
        json.dumps(test_lock, indent=2) + "\n",
    )
    put(
        "tests/backend/Host/IdentityTests.cs",
        "using Xunit;\nusing System.Runtime.CompilerServices;\npublic sealed class IdentityTests { [Fact] public void IdentityIsStable() { var plugin = (ExamplePlugin.Plugin)RuntimeHelpers.GetUninitializedObject(typeof(ExamplePlugin.Plugin)); Assert.Equal(Guid.Parse("
        + json.dumps(config["plugin"]["guid"])
        + "), plugin.Id); } }\n",
    )
    put(
        "CHANGELOG.md",
        "# Changelog\n\n## 0.1.0 ("
        + date.today().isoformat()
        + ")\n\nInitial plugin.\n",
    )
    put(
        "README.md",
        "# "
        + name
        + "\n\nRun `./plugin bootstrap`, then `./plugin dev`.\n\nUse `./plugin check`, `./plugin test`, and `./plugin build` for validation and distribution. `./plugin check` includes linting and formatting verification; `./plugin format` applies formatting. Both commands accept a target. Checks never restore dependencies; run bootstrap explicitly first. Configuration is in `plugin.toml`; toolkit updates are pinned in `toolkit.lock.json`.\n",
    )
    package = defaults["package"]
    package["name"] = identifier
    package["scripts"] = {
        key: "python3 plugin " + key
        for key in ["bootstrap", "dev", "build", "check", "format", "test"]
    }
    put("package.json", json.dumps(package, indent=4) + "\n")
    put(
        "eslint.config.mjs",
        'import js from "@eslint/js";\nimport { defineConfig } from "eslint/config";\nimport prettier from "eslint-config-prettier";\nimport globals from "globals";\nimport tseslint from "typescript-eslint";\nimport { createConfig } from "./.build/toolkit/current/configs/eslint.mjs";\nexport default createConfig({ js, defineConfig, prettier, globals, tseslint });\n',
    )
    put("requirements-check.txt", (TOOLKIT_ROOT / "requirements-check.txt").read_text())
    put(
        "pyproject.toml",
        '[tool.ruff]\nextend = ".build/toolkit/current/configs/ruff.toml"\ntarget-version = "py315"\ninclude = ["scripts/**/*.py", "tests/**/*.py"]\n',
    )

    lock_template = (
        TOOLKIT_ROOT
        / "templates"
        / ("admin" if admin else "backend")
        / "pnpm-lock.yaml"
    )
    shutil.copy2(lock_template, destination / "pnpm-lock.yaml")
    put(
        "pnpm-workspace.yaml",
        ("packages:\n  - frontend\n  - tests/browser\n" if admin else "packages: []\n")
        + 'overrides:\n  "@typescript/old": "npm:typescript@6.0.3"\nallowBuilds:\n  esbuild: true\nstrictDepBuilds: true\npmOnFail: error\nverifyDepsBeforeRun: false\nminimumReleaseAge: 1440\nminimumReleaseAgeStrict: true\n',
    )
    if admin:
        put(
            "frontend/package.json",
            json.dumps(
                {
                    "name": identifier + "-web",
                    "private": True,
                    "type": "module",
                    "devDependencies": {
                        "esbuild": "0.28.2",
                        "@typescript/native": "npm:typescript@7.0.2",
                    },
                },
                indent=4,
            )
            + "\n",
        )
        put(
            "frontend/tsconfig.json",
            json.dumps(
                {
                    "extends": "../.build/toolkit/current/configs/tsconfig.json",
                    "include": ["**/*.ts"],
                },
                indent=4,
            )
            + "\n",
        )
        put(
            "frontend/admin/config.html",
            '<div data-role="page" id="pluginSettings"><h1>'
            + name
            + '</h1><div id="pluginContent"></div><!-- PLUGIN_CONFIG_SCRIPT --></div>\n',
        )
        put(
            "frontend/admin/config.ts",
            "import {adminButton} from '../../.build/toolkit/current/ui/controls.ts';\nconst content=document.querySelector('#pluginContent');\ncontent?.append(adminButton('Ready',null));\n",
        )
        config["browser"] = {
            "directory": "tests/browser",
            "groups": "tests/browser/groups.json",
        }
        put("plugin.toml", toml(config))
        put(
            "tests/browser/package.json",
            json.dumps(
                {
                    "name": identifier + "-browser-tests",
                    "private": True,
                    "devDependencies": {"playwright": "1.64.1"},
                },
                indent=4,
            )
            + "\n",
        )
        put(
            "tests/browser/groups.json",
            json.dumps({"workflow": ["admin.spec.cjs"]}, indent=4) + "\n",
        )
        put(
            "tests/browser/playwright.config.cjs",
            "const {defineConfig}=require('playwright/test');\nmodule.exports=defineConfig({testDir:'.',testMatch:'*.spec.cjs',outputDir:process.env.CI_BROWSER_OUTPUT_DIR||'results',reporter:'list',projects:[{name:'workflow-chromium',use:{browserName:'chromium'}}]});\n",
        )
        put(
            "tests/browser/admin.spec.cjs",
            "const {test,expect}=require('playwright/test');\nconst fs=require('node:fs'),path=require('node:path');\ntest('administrator page embeds its bundled controls',async({page})=>{await page.setContent(fs.readFileSync(path.join(__dirname,'../../dist/web/config.html'),'utf8'));await expect(page.getByRole('heading',{name:"
            + json.dumps(name)
            + "})).toBeVisible();await expect(page.getByRole('button',{name:'Ready'})).toBeDisabled();});\n",
        )

    import subprocess

    metadata = TOOLKIT_ROOT / "toolkit-release.json"
    revision = (
        json.loads(metadata.read_text())["revision"]
        if metadata.exists()
        else subprocess.check_output(
            ["git", "-C", str(TOOLKIT_ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
    )
    from jellyfin_plugin_toolkit.workflows import render

    for filename, contents in render(
        lock["repository"], revision, version=lock["version"]
    ).items():
        put(".github/workflows/" + filename, contents)
    if sync_workflow:
        from jellyfin_plugin_toolkit.workflows import render_sync

        put(
            ".github/workflows/toolkit-sync.yml",
            render_sync(lock["repository"], revision, version=lock["version"]),
        )
    put(
        "release-please-config.json",
        json.dumps(
            {
                "release-type": "simple",
                "version-file": "version.txt",
                "include-component-in-tag": False,
                "force-tag-creation": True,
                "draft": True,
                "bump-minor-pre-major": True,
                "bump-patch-for-minor-pre-major": True,
                "packages": {
                    ".": {
                        "package-name": identifier,
                    }
                },
            },
            indent=4,
        )
        + "\n",
    )
    put(".release-please-manifest.json", json.dumps({".": "0.1.0"}, indent=4) + "\n")
    put(
        ".prettierrc.mjs",
        'import preset from "./.build/toolkit/current/configs/prettier.mjs";\n/** @type {import("prettier").Config} */\nconst config = preset;\nexport default config;\n',
    )
    shutil.copy2(TOOLKIT_ROOT / ".prettierignore", destination / ".prettierignore")
    print("Generated plugin consumer at", destination)
    return config
