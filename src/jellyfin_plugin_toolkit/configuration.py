"""Strict consumer inputs and their editor-facing JSON Schema."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from jellyfin_plugin_toolkit.operations import supports_default

Text = Annotated[str, StringConstraints(min_length=1, strip_whitespace=False)]
Argv = Annotated[list[Text], Field(min_length=1)]
Commands = Annotated[list[Argv], Field(min_length=1)]


class Model(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)


class Plugin(Model):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$")]
    name: Text
    guid: Text
    assembly: Text
    repository: Annotated[str, StringConstraints(pattern=r"^[\w.-]+/[\w.-]+$")]
    owner: Text
    description: Text
    overview: Text
    category: Text
    version: str | None = None

    @field_validator("guid")
    @classmethod
    def valid_guid(cls, value: str) -> str:
        uuid.UUID(value)
        return value

    @field_validator("name", "assembly", "owner", "description", "overview", "category")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Plugin identity cannot be blank")
        return value


class Host(Model):
    abi: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+\.\d+$")]
    framework: Literal["net10.0"]
    platform: Literal["linux/amd64"]
    image: Annotated[
        str, StringConstraints(pattern=r"^jellyfin/jellyfin@sha256:[0-9a-f]{64}$")
    ]


class Backend(Model):
    entry: Text
    projects: Annotated[dict[str, Text], Field(min_length=1)]
    project_assemblies: list[Text] = Field(default_factory=list)
    tests: list[Text] = Field(default_factory=list)
    source_roots: list[Text] = Field(default_factory=lambda: ["src", "tests/backend"])
    unmanaged_projects: list[
        Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+$")]
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def declared_entry(self) -> Self:
        if self.entry not in self.projects.values():
            raise ValueError("Backend entry must be a declared project")
        return self


class Architecture(Model):
    edges: dict[str, list[str]] = Field(default_factory=dict)
    forbidden: dict[str, list[str]] = Field(default_factory=dict)


class Asset(Model):
    source: Text
    name: Text


class Notice(Model):
    source: Text
    name: str | None = None


class Frontend(Model):
    enabled: bool = False
    directory: Text = "frontend"
    build: str | None = None
    assets: str | None = None
    entries: dict[str, str] = Field(default_factory=dict)
    admin_entry: str | None = None
    admin_template: str | None = None
    admin_styles: str | None = None
    boundary_check: str | None = None
    script_marker: str = "<!-- PLUGIN_CONFIG_SCRIPT -->"
    style_marker: str = "/* PLUGIN_STYLES */"
    version_symbol: str = "__PLUGIN_VERSION__"
    banners: dict[str, str] = Field(default_factory=dict)
    extra_assets: list[Asset] = Field(default_factory=list)
    required_assets: list[str] = Field(default_factory=lambda: ["config.html"])
    reload_assets: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def entrypoints(self) -> Self:
        if self.enabled and not (self.admin_entry or self.entries):
            raise ValueError("Enabled frontend has no entrypoints")
        return self


class Browser(Model):
    directory: str = "tests/browser"
    groups: str | None = None
    command: Argv = Field(
        default_factory=lambda: ["node", "node_modules/playwright/cli.js", "test"]
    )


class Sandbox(Model):
    asset_prefix: str = "/__no_plugin_assets__/"
    settings_page: str = ""


class Source(Model):
    roots: list[str] = Field(
        default_factory=lambda: [
            "src",
            "docs",
            "frontend",
            "vendor",
            "scripts",
            "tests",
            ".github",
        ]
    )
    files: list[str] = Field(default_factory=list)


class Rule(Model):
    name: str = "configured"
    patterns: list[str]
    groups: list[str]


class BrowserInputs(Model):
    """Explicit fixture-only test inputs; live-server suites use ordinary fingerprints."""

    files: Annotated[list[Text], Field(min_length=1)]
    assets: Annotated[list[Text], Field(min_length=1)] = Field(
        default_factory=lambda: ["**"]
    )
    projects: Annotated[list[Text], Field(min_length=1)]
    workers: Annotated[int, Field(ge=1)] = 1
    runner: Text = "ubuntu-24.04"


class Validation(Model):
    groups: list[Text]
    rules: list[Rule] = Field(default_factory=list)
    check_targets: list[str] = Field(
        default_factory=lambda: ["backend", "tooling", "automation"]
    )
    unit_groups: list[str] = Field(default_factory=lambda: ["backend", "tooling"])
    acceptance_groups: list[str] = Field(default_factory=list)
    job_names: dict[str, list[Text]] = Field(default_factory=dict)
    aggregate_jobs: dict[str, list[Text]] = Field(default_factory=dict)
    artifact_jobs: list[Text] = Field(
        default_factory=lambda: [
            "package",
            "installation",
            "reproducibility",
            "advisories",
        ]
    )
    browser_inputs: dict[str, BrowserInputs] = Field(default_factory=dict)
    renovate_tests: str | None = None

    @model_validator(mode="after")
    def valid_groups(self) -> Self:
        if len(set(self.groups)) != len(self.groups) or not {
            "backend",
            "artifacts",
        } <= set(self.groups):
            raise ValueError("Validation requires unique backend and artifacts groups")
        for mapping in (self.job_names, self.aggregate_jobs, self.browser_inputs):
            if set(mapping) - set(self.groups):
                raise ValueError("Validation names a disabled group")
        for jobs in [
            self.artifact_jobs,
            *self.job_names.values(),
            *self.aggregate_jobs.values(),
        ]:
            if (
                not jobs
                or len(set(jobs)) != len(jobs)
                or any(not job.strip() for job in jobs)
            ):
                raise ValueError("Validation job lists must be nonempty and unique")
        if any(set(rule.groups) - set(self.groups) for rule in self.rules):
            raise ValueError("Impact rule names a disabled validation group")
        return self


class Operation(Model):
    commands: Commands | None = None
    after: Commands | None = None
    cwd: str = "."

    @model_validator(mode="after")
    def one_kind(self) -> Self:
        if (self.commands is None) == (self.after is None):
            raise ValueError("Declare exactly one of commands or after")
        return self


class License(Model):
    license: str = "Apache-2.0"
    notices: list[str]
    package: str = "project-source"
    version: str | None = None


class LicensedPath(License):
    prefix: Text


class Distribution(Model):
    web: bool = False
    notices: list[Notice] = Field(default_factory=list)
    web_notices: list[Asset] = Field(default_factory=list)
    payload_notices: list[Asset] = Field(default_factory=list)
    licenses: dict[str, License] = Field(default_factory=dict)
    licensed_paths: list[LicensedPath] = Field(default_factory=list)
    web_exclude: list[str] = Field(default_factory=lambda: ["demo.html", "demo.wav"])


class AdvisoryWaiver(Model):
    ecosystem: Literal["npm", "nuget"]
    package: Text
    version: Text
    ghsa: Annotated[
        str, StringConstraints(pattern=r"^GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$")
    ]
    reason: Text
    issue: Annotated[str, StringConstraints(pattern=r"^https://[^/\s]+/[^\s]+$")]
    issued_at: Text
    expires_at: Text

    @field_validator("package", "version", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Waiver values must be nonblank and trimmed")
        return value

    @staticmethod
    def timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if "T" not in value or parsed.utcoffset() != timedelta(0):
            raise ValueError("Waiver timestamps must include a UTC timezone")
        return parsed

    @property
    def scope(self) -> tuple[str, str, str, str]:
        package = self.package.casefold() if self.ecosystem == "nuget" else self.package
        return self.ecosystem, package, self.version, self.ghsa

    @model_validator(mode="after")
    def lifetime(self) -> Self:
        if not re.fullmatch(
            r"[0-9]+(?:\.[0-9]+){0,3}(?:[-+][0-9A-Za-z.-]+)*", self.version
        ):
            raise ValueError("Waivers require an exact installed package version")
        pattern = (
            r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+"
            if self.ecosystem == "npm"
            else r"[A-Za-z0-9._-]+"
        )
        if not re.fullmatch(pattern, self.package):
            raise ValueError("Waivers require an exact package name")
        issued = self.timestamp(self.issued_at)
        expires = self.timestamp(self.expires_at)
        if issued > datetime.now(UTC):
            raise ValueError("Waiver issuance cannot be in the future")
        if not issued < expires <= issued + timedelta(days=30):
            raise ValueError("Waiver lifetime must be positive and at most 30 days")
        return self


class Advisories(Model):
    fail_on_findings: bool = False
    waivers: list[AdvisoryWaiver] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self) -> Self:
        scopes = [waiver.scope for waiver in self.waivers]
        if len(scopes) != len(set(scopes)):
            raise ValueError("Duplicate advisory waiver scope")
        return self


class Configuration(Model):
    schema_version: Literal[1] = Field(alias="schema")
    plugin: Plugin
    host: Host
    backend: Backend
    validation: Validation
    images: dict[str, str] = Field(default_factory=dict)
    architecture: Architecture = Field(default_factory=Architecture)
    frontend: Frontend = Field(default_factory=Frontend)
    browser: Browser = Field(default_factory=Browser)
    sandbox: Sandbox = Field(default_factory=Sandbox)
    source: Source = Field(default_factory=Source)
    distribution: Distribution = Field(default_factory=Distribution)
    advisories: Advisories = Field(default_factory=Advisories)
    operations: dict[str, Operation] = Field(default_factory=dict)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema(cls, value: object) -> int:
        # Literal equality otherwise accepts True and 1.0 even in strict models.
        if type(value) is not int:
            raise ValueError("Configuration schema must be an integer")
        return value

    @model_validator(mode="after")
    def hooks(self) -> Self:
        if {"actionlint", "renovate"}.intersection(self.images):
            raise ValueError(
                "Automation validator pins are toolkit-owned; remove local overrides"
            )
        for name, operation in self.operations.items():
            if operation.after is not None and not supports_default(name):
                raise ValueError("After hook requires a standard operation: " + name)
        return self

    def data(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_unset=True
        )
