"""CLI shim — prefer `python -m app.cli`."""

from app.cli import build_parser, main

if __name__ == "__main__":
    main()
