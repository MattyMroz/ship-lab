import argparse
import sys
from pathlib import Path

from lab.parser import parse_lines


def main() -> int:
    """Print the number of non-empty lines of the file named by the first argument."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="python -m lab.cli", description="Count the non-empty lines of a file."
    )
    parser.add_argument("path", type=Path, help="file to count")
    path: Path = parser.parse_args().path
    try:
        text: str = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        parser.error(f"file not found: {path}")
    print(len(parse_lines(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
