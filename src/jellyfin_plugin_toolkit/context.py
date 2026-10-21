"""Consumer configuration; toolkit installation paths never identify a plugin root."""

import json
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jellyfin_plugin_toolkit.configuration import Configuration
from jellyfin_plugin_toolkit.resources_api import RESOURCE_ROOT

TOOLKIT_ROOT = RESOURCE_ROOT


def relative(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Configured path must be a nonempty relative path")
    path = root / value
    if Path(value).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(
            "Configured path must stay inside the plugin repository: " + value
        )
    return path


def output_name(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid output path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or value == "."
    ):
        raise ValueError("Invalid output path: " + value)
    return value


def validate_paths(root, config: Configuration):
    backend, frontend, browser = config.backend, config.frontend, config.browser
    distribution = config.distribution
    paths = [
        backend.entry,
        *backend.projects.values(),
        *backend.tests,
        *backend.source_roots,
        *config.source.roots,
        *config.source.files,
        browser.directory,
    ]
    paths.extend(
        getattr(frontend, key)
        for key in (
            "directory",
            "build",
            "assets",
            "admin_template",
            "admin_styles",
            "boundary_check",
        )
        if getattr(frontend, key) is not None
    )
    if browser.groups:
        paths.append(browser.groups)
    for project, dependencies in config.architecture.edges.items():
        paths.extend([project, *dependencies])
    paths.extend(config.architecture.forbidden)
    paths.extend(operation.cwd for operation in config.operations.values())
    base = relative(root, frontend.directory)
    for entry, output in frontend.entries.items():
        relative(base, entry)
        output_name(output)
    if frontend.admin_entry:
        relative(base, frontend.admin_entry)
    for asset in frontend.extra_assets:
        paths.append(asset.source)
        output_name(asset.name)
    for name in [*frontend.required_assets, *frontend.reload_assets]:
        output_name(name)
    for entry in [*distribution.notices, *distribution.web_notices]:
        paths.append(entry.source)
        output_name(entry.name or Path(entry.source).name)
    for entry in distribution.payload_notices:
        output_name(entry.source)
        output_name(entry.name)
    for entry in [*distribution.licenses.values(), *distribution.licensed_paths]:
        for name in entry.notices:
            output_name(name)
    for entry in distribution.licensed_paths:
        output_name(entry.prefix.rstrip("/"))
    for value in paths:
        relative(root, value)
    for spec in config.validation.browser_inputs.values():
        for pattern in spec.files:
            relative(root, pattern)


@dataclass(frozen=True)
class Context:
    root: Path
    config: Configuration

    def __post_init__(self):
        if isinstance(self.config, dict):
            object.__setattr__(
                self, "config", Configuration.model_validate(self.config)
            )

    @classmethod
    def load(cls, root: Path | None = None):
        if root is None:
            selected = os.environ.get("JELLYFIN_PLUGIN_ROOT")
            root = Path(selected) if selected else Path.cwd()
            if not selected:
                root = next(
                    (p for p in (root, *root.parents) if (p / "plugin.toml").is_file()),
                    root,
                )
        root = root.resolve()
        with (root / "plugin.toml").open("rb") as source:
            config = Configuration.model_validate(tomllib.load(source))
        validate_paths(root, config)
        version = (root / "version.txt").read_text().strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("Invalid version.txt")
        if config.plugin.version and config.plugin.version != version:
            raise ValueError("plugin.version conflicts with authoritative version.txt")
        for path in [
            root / "Directory.Build.props",
            *[root / value for value in config.backend.projects.values()],
        ]:
            if not path.is_file():
                continue
            tree = ET.parse(path)
            for element in tree.findall(".//Version") + tree.findall(
                ".//PluginVersion"
            ):
                text = (element.text or "").strip()
                if text and "$(" not in text and text != version:
                    raise ValueError("Conflicting version declaration: " + str(path))
        host_project = root / config.backend.entry
        if host_project.is_file():
            assembly = ET.parse(host_project).find(".//AssemblyName")
            if assembly is not None and assembly.text != config.plugin.assembly:
                raise ValueError("Host AssemblyName conflicts with plugin.assembly")
        return cls(root, config)

    @property
    def identity(self):
        return self.config.plugin

    @property
    def version(self):
        return (self.root / "version.txt").read_text().strip()

    @property
    def groups(self):
        return tuple(self.config.validation.groups)

    @property
    def images(self):
        return {
            **self.config.images,
            "jellyfin": self.config.host.image,
            "platform": self.config.host.platform,
        }

    @property
    def prefix(self):
        return self.identity.id

    def path(self, value):
        return relative(self.root, value)

    def generated(self):
        from jellyfin_plugin_toolkit.resources_api import prepare

        prepare(self.root)
        import xml.etree.ElementTree as ET

        output = self.root / ".build/generated"
        output.mkdir(parents=True, exist_ok=True)
        project = ET.Element("Project")
        properties = ET.SubElement(project, "PropertyGroup")
        for key, value in {
            "PluginVersion": self.version,
            "Version": self.version,
            "PluginGuid": self.identity.guid,
            "PluginName": self.identity.name,
            "JellyfinTargetAbi": self.config.host.abi,
            "PluginTargetFramework": self.config.host.framework,
        }.items():
            ET.SubElement(properties, key).text = value
        for name in self.config.backend.unmanaged_projects:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                raise ValueError("Invalid unmanaged project name")
            group = ET.SubElement(
                project,
                "PropertyGroup",
                {"Condition": "'$(MSBuildProjectName)' == '" + name + "'"},
            )
            ET.SubElement(group, "PluginManagedProject").text = "false"
        ET.ElementTree(project).write(
            output / "PluginIdentity.props", encoding="unicode"
        )
        data = {**self.config.data(), "version": self.version, "root": str(self.root)}
        (output / "plugin.json").write_text(json.dumps(data, indent=2) + "\n")
        return output
