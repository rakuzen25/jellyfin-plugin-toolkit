"""Consumer configuration; toolkit installation paths never identify a plugin root."""

import json
import os
import re
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent


def relative(root: Path, value: str) -> Path:
    path = root / value
    if Path(value).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(
            "Configured path must stay inside the plugin repository: " + value
        )
    return path


@dataclass(frozen=True)
class Context:
    root: Path
    config: dict

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
            config = tomllib.load(source)
        if config.get("schema") != 1:
            raise ValueError("Unsupported plugin configuration schema")
        identity = config.get("plugin", {})
        for key in (
            "id",
            "name",
            "guid",
            "assembly",
            "repository",
            "owner",
            "description",
            "overview",
            "category",
        ):
            if not isinstance(identity.get(key), str) or not identity[key].strip():
                raise ValueError("Missing plugin." + key)
        if not re.fullmatch(r"[a-z][a-z0-9-]*", identity["id"]):
            raise ValueError("Invalid plugin id")
        uuid.UUID(identity["guid"])
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", identity["repository"]):
            raise ValueError("Invalid GitHub repository")
        host = config.get("host", {})
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host.get("abi", "")):
            raise ValueError("Invalid host ABI")
        if host.get("framework") != "net10.0" or host.get("platform") != "linux/amd64":
            raise ValueError("The initial toolkit supports net10.0 on linux/amd64")
        if not re.fullmatch(
            r"jellyfin/jellyfin@sha256:[0-9a-f]{64}", host.get("image", "")
        ):
            raise ValueError("Pin the official Jellyfin image by SHA-256")
        version = (root / "version.txt").read_text().strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("Invalid version.txt")
        projects = config.get("backend", {}).get("projects", {})
        entry = config.get("backend", {}).get("entry")
        if not entry or not projects:
            raise ValueError("Declare backend.entry and backend.projects")
        for value in [
            entry,
            *projects.values(),
            *config.get("source", {}).get("roots", []),
        ]:
            relative(root, value)
        if entry not in projects.values():
            raise ValueError("Backend entry must be a declared project")
        groups = config.get("validation", {}).get("groups", [])
        if (
            len(groups) != len(set(groups))
            or "backend" not in groups
            or "artifacts" not in groups
        ):
            raise ValueError("Validation requires unique backend and artifacts groups")
        for name, operation in config.get("operations", {}).items():
            commands = operation.get("commands", [])
            if not isinstance(commands, list) or any(
                not isinstance(c, list)
                or not c
                or not all(isinstance(a, str) for a in c)
                for c in commands
            ):
                raise ValueError(
                    "Operations contain argv arrays, not shell commands: " + name
                )
        frontend = config.get("frontend", {})
        for value in [
            *config["backend"].get("tests", []),
            *config["backend"].get("source_roots", []),
        ]:
            relative(root, value)
        for key in (
            "directory",
            "build",
            "admin_template",
            "admin_styles",
            "boundary_check",
        ):
            if value := frontend.get(key):
                relative(root, value)
        for value in frontend.get("entries", {}).values():
            relative(root, value)
        for entry in frontend.get("extra_assets", []):
            relative(root, entry["source"])
            relative(root, entry["name"])
        for entry in config.get("distribution", {}).get("notices", []):
            relative(root, entry["source"])
        for rule in config["validation"].get("rules", []):
            if set(rule.get("groups", [])) - set(groups):
                raise ValueError("Impact rule names a disabled validation group")
        if (
            frontend.get("enabled")
            and not frontend.get("admin_entry")
            and not frontend.get("entries")
        ):
            raise ValueError("Enabled frontend has no entrypoints")
        if frontend.get("admin_entry"):
            relative(
                root,
                frontend.get("directory", "frontend") + "/" + frontend["admin_entry"],
            )
        if duplicate := identity.get("version"):
            if duplicate != version:
                raise ValueError(
                    "plugin.version conflicts with authoritative version.txt"
                )
        import xml.etree.ElementTree as ET

        owned = [
            root / "Directory.Build.props",
            *[root / value for value in projects.values()],
        ]
        for path in owned:
            if not path.is_file():
                continue
            tree = ET.parse(path)
            for element in tree.findall(".//Version") + tree.findall(
                ".//PluginVersion"
            ):
                text = (element.text or "").strip()
                if text and "$(" not in text and text != version:
                    raise ValueError("Conflicting version declaration: " + str(path))
        host_project = root / config["backend"]["entry"]
        if host_project.is_file():
            assembly = ET.parse(host_project).find(".//AssemblyName")
            if assembly is not None and assembly.text != identity["assembly"]:
                raise ValueError("Host AssemblyName conflicts with plugin.assembly")
        return cls(root, config)

    @property
    def identity(self):
        return self.config["plugin"]

    @property
    def version(self):
        return (self.root / "version.txt").read_text().strip()

    @property
    def groups(self):
        return tuple(self.config["validation"]["groups"])

    @property
    def prefix(self):
        return self.identity["id"]

    def path(self, value):
        return relative(self.root, value)

    def generated(self):
        import xml.etree.ElementTree as ET

        output = self.root / ".build/generated"
        output.mkdir(parents=True, exist_ok=True)
        project = ET.Element("Project")
        properties = ET.SubElement(project, "PropertyGroup")
        for key, value in {
            "PluginVersion": self.version,
            "Version": self.version,
            "PluginGuid": self.identity["guid"],
            "PluginName": self.identity["name"],
            "JellyfinTargetAbi": self.config["host"]["abi"],
            "PluginTargetFramework": self.config["host"]["framework"],
        }.items():
            ET.SubElement(properties, key).text = value
        for name in self.config["backend"].get("unmanaged_projects", []):
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
        data = {**self.config, "version": self.version, "root": str(self.root)}
        (output / "plugin.json").write_text(json.dumps(data, indent=2) + "\n")
        from jellyfin_plugin_toolkit.checks.pins import check_pins

        check_pins(self.root)
        return output
