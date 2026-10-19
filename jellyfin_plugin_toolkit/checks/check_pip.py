"""Require pip support for the shared release-age policy and Python 3.15."""

import re
import sys
from importlib.metadata import PackageNotFoundError, version


def main() -> int:
    try:
        installed = version("pip")
    except PackageNotFoundError:
        installed = "missing"
    release = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", installed)
    if release is None or tuple(map(int, release.groups())) < (26, 2):
        print(
            f"pip >=26.2 required for the 24-hour release-age cutoff and Python 3.15; found {installed}. "
            f"Upgrade with: {sys.executable} -m pip install --upgrade 'pip>=26.2'",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
