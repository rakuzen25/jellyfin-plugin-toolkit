"""Generate small consumers of the foundation without copying its implementation."""

import json
import re
import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import tomli_w

from jellyfin_plugin_toolkit.configuration import Configuration
from jellyfin_plugin_toolkit.context import TOOLKIT_ROOT, relative


def toml(config: Configuration | dict[str, Any]) -> str:
    return tomli_w.dumps(config.data() if isinstance(config, Configuration) else config)


def generate(
    context: object,
    destination: Path,
    identifier: str,
    name: str,
    repository: str,
    admin: bool = False,
    host_project: str = "src/Host/Plugin.csproj",
    *,
    toolkit_wheel: Path | None = None,
    toolkit_revision: str | None = None,
    sync_workflow: bool = False,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", identifier):
        raise ValueError("Invalid plugin id")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid repository identity")
    if destination.exists():
        raise ValueError("Starter destination already exists")
    relative(destination, host_project)
    defaults = json.loads((TOOLKIT_ROOT / "templates/defaults.json").read_text())
    from jellyfin_plugin_toolkit.provenance import (
        REPOSITORY,
        TOOLKIT_DIST,
        package_version,
        release_url,
    )

    selected = package_version()
    lock = {"repository": REPOSITORY, "version": "v" + selected}
    import subprocess

    from jellyfin_plugin_toolkit.provenance import public_revision

    revision = toolkit_revision or public_revision(selected)
    assembly = "Jellyfin.Plugin." + "".join(
        p.capitalize() for p in identifier.split("-")
    )
    source_root = Path(host_project).parts[0]
    groups = ["metadata", "backend", "tooling", "artifacts"] + (
        ["frontend", "workflow"] if admin else []
    )
    test = "tests/backend/Host/Plugin.Tests.csproj"
    config: dict[str, Any] = {
        "schema": 1,
        "advisories": {"waivers": []},
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
                        "uv.lock",
                        "pyproject.toml",
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

    def put(name: str, text: str) -> None:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    put(
        "plugin.toml",
        "#:schema .build/jpt/configs/plugin.schema.json\n" + toml(config),
    )
    put("version.txt", "0.1.0\n")
    put(
        "renovate.json",
        json.dumps(
            {
                "$schema": "https://docs.renovatebot.com/renovate-schema.json",
                "extends": [
                    "github>" + lock["repository"] + ":tooling#" + lock["version"],
                    "github>" + lock["repository"] + "#" + lock["version"],
                ],
            },
            indent=4,
        )
        + "\n",
    )
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
        ".build/\ndist/\n.venv/\nnode_modules/\n**/bin/\n**/obj/\n**/__pycache__/\n**/results-run.*/\n**/TestResults/\n.ruff_cache/\n.mypy_cache/\n",
    )
    put(
        "Directory.Build.props",
        '<Project>\n  <PropertyGroup><PluginRepositoryRoot>$(MSBuildThisFileDirectory)</PluginRepositoryRoot></PropertyGroup>\n  <Import Project=".build/jpt/msbuild/Plugin.props" />\n</Project>\n',
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
        + "\n\nRun `uv run jpt bootstrap`, then `uv run jpt dev`.\n\nUse `uv run jpt check`, `uv run jpt test`, and `uv run jpt build` for validation and distribution. `uv run jpt check` includes linting and formatting verification; `uv run jpt format` applies formatting. Both commands accept a target. uv synchronizes Python dependencies automatically; run bootstrap to prepare Node, .NET and Jellyfin integrations first. Configuration is in `plugin.toml`; toolkit dependencies are recorded by uv in `pyproject.toml` and `uv.lock`.\n\nRepository writes require your existing GitHub App. Configure Actions variable `PLUGIN_APP_CLIENT_ID` and secret `PLUGIN_APP_PRIVATE_KEY`; there is no workflow-token fallback. After configuring those credentials, run the App setup workflow. It opens a reviewed PR configuring Renovate automatically.\n",
    )
    package = defaults["package"]
    package["name"] = identifier
    package["scripts"] = {
        key: "uv run jpt " + key
        for key in ["bootstrap", "dev", "build", "check", "format", "test"]
    }
    put("package.json", json.dumps(package, indent=4) + "\n")
    put(
        "eslint.config.mjs",
        'import js from "@eslint/js";\nimport { defineConfig } from "eslint/config";\nimport prettier from "eslint-config-prettier";\nimport globals from "globals";\nimport tseslint from "typescript-eslint";\nimport { createConfig } from "./.build/jpt/configs/eslint.mjs";\nexport default createConfig({ js, defineConfig, prettier, globals, tseslint });\n',
    )
    # Let uv create project metadata and maintain the dependency graph itself.
    subprocess.run(
        [
            "uv",
            "init",
            "--bare",
            "--no-package",
            "--no-workspace",
            "--vcs",
            "none",
            "--name",
            identifier + "-tooling",
            "--python",
            "3.15.0",
            str(destination),
        ],
        check=True,
    )
    project = destination / "pyproject.toml"
    with project.open("a") as output:
        output.write(
            '\n[tool.uv]\nrequired-version = "==0.13.1"\npython-preference = "only-managed"\nexclude-newer = "1 day"\n'
        )
        output.write(
            '\n[tool.ruff]\nextend = ".build/jpt/configs/ruff.toml"\ntarget-version = "py315"\ninclude = ["scripts/**/*.py", "tests/**/*.py"]\n'
        )
    with project.open("a") as output:
        output.write('\n[tool.mypy]\npython_version = "3.15"\nfiles = ["tests"]\n')
    with project.open("a") as output:
        output.write(
            '\n[tool.basedpyright]\nextends = ".build/jpt/configs/basedpyright.toml"\nvenvPath = "."\nvenv = ".venv"\npythonVersion = "3.15"\ninclude = ["tests"]\n'
        )
    from jellyfin_plugin_toolkit.typing_policy import materialize

    project.write_text(materialize(project.read_text()))
    source = release_url(selected)
    if toolkit_wheel is not None:
        wheel = Path(toolkit_wheel).resolve()
        target = destination / TOOLKIT_DIST / wheel.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wheel, target)
        sdist = wheel.with_name(f"jellyfin_plugin_toolkit-{selected}.tar.gz")
        if not sdist.is_file():
            raise ValueError(
                "Candidate wheel must have its matching uv-built source distribution"
            )
        shutil.copy2(sdist, target.with_name(sdist.name))
        source = "./" + (TOOLKIT_DIST / wheel.name).as_posix()
    subprocess.run(["uv", "add", "--no-sync", source], cwd=destination, check=True)
    subprocess.run(
        [
            "uv",
            "add",
            "--dev",
            "--no-sync",
            "ruff>=0.17.1,<0.18",
            "mypy>=2.4,<3",
            "basedpyright>=1.40.2,<2",
        ],
        cwd=destination,
        check=True,
    )
    from jellyfin_plugin_toolkit.resources_api import prepare

    prepare(destination)

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
                    "extends": "../.build/jpt/configs/tsconfig.json",
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
            "import {adminButton} from '../../.build/jpt/ui/controls.ts';\nconst content=document.querySelector('#pluginContent');\ncontent?.append(adminButton('Ready',null));\n",
        )
        config["browser"] = {
            "directory": "tests/browser",
            "groups": "tests/browser/groups.json",
        }
        put(
            "plugin.toml",
            "#:schema .build/jpt/configs/plugin.schema.json\n" + toml(config),
        )
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
    from jellyfin_plugin_toolkit.release.configuration import configure

    configure(destination, apply=True)
    put(
        ".prettierrc.mjs",
        'import preset from "./.build/jpt/configs/prettier.mjs";\n/** @type {import("prettier").Config} */\nconst config = preset;\nexport default config;\n',
    )
    shutil.copy2(TOOLKIT_ROOT / ".prettierignore", destination / ".prettierignore")
    print("Generated plugin consumer at", destination)
    return config
