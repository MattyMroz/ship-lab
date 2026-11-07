import json
import os
import sys
from pathlib import Path
from typing import Final

PREFIXES: Final[tuple[str, ...]] = ("PRE_COMMIT", "GITHUB_HEAD_REF", "GITHUB_REF", "GITHUB_EVENT_NAME")
"""Prefixes of the environment variables that tell a pre-push hook what is pushed."""


def main() -> int:
    """Print what the hook gets from prek, append it to PROBE_OUT and fail when PROBE_BLOCK is set."""
    stdin: str = sys.stdin.read() if sys.stdin is not None and not sys.stdin.isatty() else ""
    seen: dict[str, object] = {
        "argv": sys.argv[1:],
        "env": {name: value for name, value in sorted(os.environ.items()) if name.startswith(PREFIXES)},
        "stdin": stdin,
    }
    line: str = json.dumps(seen)
    print(line)
    out: str = os.environ.get("PROBE_OUT", "")
    if out:
        with Path(out).open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    return 1 if os.environ.get("PROBE_BLOCK") else 0


if __name__ == "__main__":
    sys.exit(main())
