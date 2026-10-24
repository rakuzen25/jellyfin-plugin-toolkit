"""Runtime JSON container checks with explicit dynamic element types."""

from typing import Any, TypeGuard, cast


def is_json_object(value: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict) and all(
        isinstance(key, str) for key in cast(dict[object, object], value)
    )


def is_json_array(value: object) -> TypeGuard[list[Any]]:
    return isinstance(value, list)
