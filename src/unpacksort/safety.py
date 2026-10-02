"""Portable path validation and deterministic naming."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import PurePosixPath

WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DRIVE = re.compile(r"^[A-Za-z]:")
MAX_COMPONENT = 180
MAX_RELATIVE_PATH = 220
MAX_PRESERVED_SUFFIX = 32
MIN_HASHED_COMPONENT = 10


def portable_text_key(value: str) -> tuple[str, bytes]:
    """Order text consistently across platforms and locales."""

    normalized = unicodedata.normalize("NFC", value)
    return normalized.casefold(), normalized.encode("utf-8", "surrogatepass")


def unsafe_logical_path(value: str) -> bool:
    """Return whether an untrusted logical path is unsafe to materialize."""

    if "\x00" in value or DRIVE.match(value) or value.startswith(("/", "\\", "//", "\\\\")):
        return True
    normalized = value.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    return any(part in {"", ".", ".."} or ":" in part for part in parts)


def safe_component(value: str | None, *, fallback: str = "unnamed") -> tuple[str, tuple[str, ...]]:
    """Create one portable bounded component while preserving reasons."""

    reasons: list[str] = []
    original = value or ""
    candidate = unicodedata.normalize("NFC", original)
    if candidate != original:
        reasons.append("unicode_nfc")
    if not candidate:
        candidate = fallback
        reasons.append("missing_name")
    replaced = FORBIDDEN.sub("_", candidate)
    if replaced != candidate:
        reasons.append("forbidden_characters")
    candidate = replaced.rstrip(" .")
    if not candidate:
        candidate = fallback
        reasons.append("empty_after_normalization")
    stem = candidate.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED:
        candidate = f"_{candidate}"
        reasons.append("windows_reserved")
    if len(candidate.encode("utf-8")) > MAX_COMPONENT:
        candidate = _truncate_component(candidate, MAX_COMPONENT)
        reasons.append("component_truncated")
    return candidate, tuple(reasons)


def collision_key(value: str) -> str:
    """Return a platform-independent collision key."""

    return unicodedata.normalize("NFC", value).casefold()


def suffixed_name(name: str, number: int) -> str:
    """Insert an exact numeric suffix before the final extension."""

    path = PurePosixPath(name)
    if path.suffix:
        return f"{path.stem}_{number}{path.suffix}"
    return f"{name}_{number}"


def bounded_relative_path(parts: list[str], *, protected_prefix: int = 1) -> str:
    """Bound the complete portable path without platform-specific behavior."""

    safe_parts = [safe_component(part)[0] for part in parts]
    result = "/".join(safe_parts)
    if len(result.encode("utf-8")) <= MAX_RELATIVE_PATH:
        return result
    if not 1 <= protected_prefix < len(safe_parts):
        msg = "protected path prefix must leave a filename component"
        raise ValueError(msg)
    prefix = safe_parts[:protected_prefix]
    name = safe_parts[-1]
    parent_hash = hashlib.sha256(
        "/".join(safe_parts[protected_prefix:-1]).encode("utf-8"),
    ).hexdigest()[:12]
    compact_parent = [*prefix, f"ancestry-{parent_hash}"]
    parent_bytes = len("/".join(compact_parent).encode("utf-8")) + 1
    available = min(MAX_COMPONENT, MAX_RELATIVE_PATH - parent_bytes)
    if available < MIN_HASHED_COMPONENT:
        msg = "protected path prefix leaves no room for a safe filename"
        raise ValueError(msg)
    return "/".join([*compact_parent, _truncate_component(name, available)])


def _truncate_component(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    suffix = PurePosixPath(value).suffix
    suffix_bytes = suffix.encode("utf-8")
    if len(suffix_bytes) > MAX_PRESERVED_SUFFIX:
        suffix = ""
        suffix_bytes = b""
    digest = hashlib.sha256(encoded).hexdigest()[:8]
    marker = f"-{digest}"
    budget = max_bytes - len(marker) - len(suffix_bytes)
    prefix = encoded[: max(1, budget)].decode("utf-8", "ignore")
    if not prefix:
        prefix = "f"
    return f"{prefix}{marker}{suffix}"


def paths_alias(left: os.PathLike[str], right: os.PathLike[str]) -> bool:
    """Compare paths even when one does not yet exist."""

    left_path = os.path.normcase(os.path.realpath(os.fspath(left))).casefold()
    right_path = os.path.normcase(os.path.realpath(os.fspath(right))).casefold()
    return left_path == right_path
