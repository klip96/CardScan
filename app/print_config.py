"""Small helper for .bat scripts: prints one config.yaml value by dotted path.

Used by run.bat instead of embedding a parenthesis-heavy Python one-liner
directly inside a `for /f` command substitution — cmd.exe's naive parser
can miscount nested parentheses in that context and corrupt the script.

Usage: python app\\print_config.py <dotted.path> [default]

Python 3.9-compatible.
"""
import sys

from app.config import Config


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    default = sys.argv[2] if len(sys.argv) > 2 else ""
    value = Config.load().get(path, default)
    print(value if value else default)


if __name__ == "__main__":
    main()
