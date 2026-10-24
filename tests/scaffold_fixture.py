"""Build the actual uv package once and use native local-wheel dependencies in fixtures."""

import functools
import subprocess
from pathlib import Path
from typing import Any

from jellyfin_plugin_toolkit.scaffold import generate as generate_package

ROOT = Path(__file__).resolve().parents[1]


@functools.cache
def candidate() -> Path:
    subprocess.run(
        ["uv", "build", "--out-dir", str(ROOT / ".build/test-wheels")],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    from jellyfin_plugin_toolkit.provenance import wheel_name

    return ROOT / ".build/test-wheels" / wheel_name()


def generate(*args: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("toolkit_wheel", candidate())
    kwargs.setdefault("toolkit_revision", "a" * 40)
    return generate_package(*args, **kwargs)
