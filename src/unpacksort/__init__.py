"""Deterministic mail and archive recovery."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("unpacksort")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["__version__"]
