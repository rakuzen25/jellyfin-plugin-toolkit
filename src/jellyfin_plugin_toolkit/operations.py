"""Declarative replacement and after-default operations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jellyfin_plugin_toolkit.configuration import Configuration

CHECK_TARGETS = frozenset({"backend", "docs", "frontend", "tooling"})
TEST_TARGETS = frozenset(
    {
        "backend",
        "frontend",
        "tooling",
        "browser",
        "reproducibility",
        "advisories",
        "web-installation",
        "artifact-inventory",
        "installation",
    }
)


def supports_default(name: str) -> bool:
    for prefix, targets in (
        ("format-check-", CHECK_TARGETS),
        ("format-", CHECK_TARGETS),
        ("check-", CHECK_TARGETS),
        ("test-", TEST_TARGETS),
    ):
        if name.startswith(prefix):
            return name[len(prefix) :] in targets
    return False


def replaces(config: Configuration, name: str) -> bool:
    operation = config.operations.get(name)
    return operation is not None and operation.commands is not None
