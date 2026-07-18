"""Backward-compatible shims — prefer importing from `app`."""

from app.config.settings import Settings

__all__ = ["Settings"]
