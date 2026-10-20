"""Declarative replacement and after-default operations."""

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


def supports_default(name):
    for prefix, targets in (
        ("format-check-", CHECK_TARGETS),
        ("format-", CHECK_TARGETS),
        ("check-", CHECK_TARGETS),
        ("test-", TEST_TARGETS),
    ):
        if name.startswith(prefix):
            return name[len(prefix) :] in targets
    return False


def replaces(config, name):
    return "commands" in config.get("operations", {}).get(name, {})


def validate_operations(operations):
    if not isinstance(operations, dict):
        raise ValueError("Operations must be a table")
    for name, spec in operations.items():
        if not isinstance(spec, dict) or ("commands" in spec) == ("after" in spec):
            raise ValueError("Declare exactly one of commands or after: " + name)
        key = "commands" if "commands" in spec else "after"
        commands = spec[key]
        if (
            not isinstance(commands, list)
            or not commands
            or any(
                not isinstance(command, list)
                or not command
                or any(not isinstance(argument, str) for argument in command)
                for command in commands
            )
        ):
            raise ValueError(
                "Operations contain argv arrays, not shell commands: " + name
            )
        if key == "after" and not supports_default(name):
            raise ValueError("After hook requires a standard operation: " + name)
