"""Consumer configuration; toolkit installation paths never identify a plugin root."""

import json
import os
import re
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent


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
    """Canonical names relative to a generated asset or distribution directory."""
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


def validate_paths(root, config):
    backend = config["backend"]
    frontend = config.get("frontend", {})
    browser = config.get("browser", {})
    distribution = config.get("distribution", {})
    paths = [
        *backend.get("tests", []),
        *backend.get("source_roots", []),
        *config.get("source", {}).get("files", []),
    ]
    for key in (
        "directory",
        "build",
        "assets",
        "admin_template",
        "admin_styles",
        "boundary_check",
    ):
        if key in frontend:
            paths.append(frontend[key])
    for key in ("directory", "groups"):
        if key in browser:
            paths.append(browser[key])
    architecture = config.get("architecture", {})
    for project, dependencies in architecture.get("edges", {}).items():
        paths.extend([project, *dependencies])
    paths.extend(architecture.get("forbidden", {}))
    for operation in config.get("operations", {}).values():
        paths.append(operation.get("cwd", "."))
    frontend_base = relative(root, frontend.get("directory", "frontend"))
    for entry, output in frontend.get("entries", {}).items():
        relative(frontend_base, entry)
        output_name(output)
    if frontend.get("admin_entry"):
        relative(frontend_base, frontend["admin_entry"])
    for asset in frontend.get("extra_assets", []):
        paths.append(asset["source"])
        output_name(asset["name"])
    for name in [
        *frontend.get("required_assets", []),
        *frontend.get("reload_assets", []),
    ]:
        output_name(name)
    for key in ("notices", "web_notices"):
        for entry in distribution.get(key, []):
            paths.append(entry["source"])
            output_name(entry.get("name", Path(entry["source"]).name))
    for entry in distribution.get("payload_notices", []):
        output_name(entry["source"])
        output_name(entry["name"])
    for entry in distribution.get("licenses", {}).values():
        for name in entry.get("notices", []):
            output_name(name)
    for entry in distribution.get("licensed_paths", []):
        output_name(entry["prefix"].rstrip("/"))
        for name in entry.get("notices", []):
            output_name(name)
    for value in paths:
        relative(root, value)


def validate_jobs(validation, groups):
    for key in ("job_names", "aggregate_jobs"):
        mapping = validation.get(key, {})
        if not isinstance(mapping, dict) or set(mapping) - set(groups):
            raise ValueError("Invalid validation." + key)
        for jobs in mapping.values():
            validate_job_list(jobs, key)
    if "artifact_jobs" in validation:
        validate_job_list(validation["artifact_jobs"], "artifact_jobs")


def validate_job_list(jobs, key):
    if (
        not isinstance(jobs, list)
        or not jobs
        or any(not isinstance(job, str) or not job.strip() for job in jobs)
        or len(jobs) != len(set(jobs))
    ):
        raise ValueError("Validation job lists must be nonempty and unique: " + key)


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
            raise ValueError("The toolkit supports net10.0 on linux/amd64")
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
            not isinstance(groups, list)
            or any(not isinstance(group, str) or not group.strip() for group in groups)
            or len(groups) != len(set(groups))
            or "backend" not in groups
            or "artifacts" not in groups
        ):
            raise ValueError("Validation requires unique backend and artifacts groups")
        for name, operation in config.get("operations", {}).items():
            commands = operation.get("commands", [])
            if (
                not isinstance(commands, list)
                or not commands
                or any(
                    not isinstance(c, list)
                    or not c
                    or not all(isinstance(a, str) for a in c)
                    for c in commands
                )
            ):
                raise ValueError(
                    "Operations contain argv arrays, not shell commands: " + name
                )
        frontend = config.get("frontend", {})
        validate_paths(root, config)
        validate_jobs(config["validation"], groups)
        for rule in config["validation"].get("rules", []):
            if set(rule.get("groups", [])) - set(groups):
                raise ValueError("Impact rule names a disabled validation group")
        if (
            frontend.get("enabled")
            and not frontend.get("admin_entry")
            and not frontend.get("entries")
        ):
            raise ValueError("Enabled frontend has no entrypoints")
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
    def images(self):
        return {
            **self.config.get("images", {}),
            "jellyfin": self.config["host"]["image"],
            "platform": self.config["host"]["platform"],
        }

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
        return output
