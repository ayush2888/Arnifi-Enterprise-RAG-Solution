"""Server shim — prefer `python -m app.api.server`."""

from app.api.server import app, main

if __name__ == "__main__":
    main()
