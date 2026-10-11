"""Open source files only as regular files confined below their source root.

Inventory rejects links when it walks the tree, but a path or one of its
ancestors can be replaced afterwards. Every later read therefore re-proves,
on the handle it actually reads from, that it is a regular file below the
root reached without following a link. A substitution fails closed: the read
is refused and the source stays untouched.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import stat
from pathlib import Path
from typing import Any, BinaryIO, cast

# Read through getattr so one module type-checks on every platform; the
# descriptor walk that needs them only runs where os.name is "posix".
_O_CLOEXEC: int = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK: int = getattr(os, "O_NONBLOCK", 0)
_O_BINARY: int = getattr(os, "O_BINARY", 0)
_O_NOINHERIT: int = getattr(os, "O_NOINHERIT", 0)
_EXTENDED_UNC = "\\\\?\\UNC\\"
_EXTENDED = "\\\\?\\"


class UnsafeSourceError(OSError):
    """A source path stopped being a regular, root-confined, link-free file."""


def open_source(path: Path, root: Path) -> BinaryIO:
    """Open *path* for binary reading, proving it is a regular file below *root*."""

    relative = path.relative_to(root)
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        msg = f"source path escapes its root: {path}"
        raise UnsafeSourceError(msg)
    if os.name == "nt":
        descriptor = _open_by_identity(path, root, relative)
    else:
        descriptor = _open_by_descriptor_walk(root, relative)
    return os.fdopen(descriptor, "rb")


def _open_by_descriptor_walk(root: Path, relative: Path) -> int:
    """POSIX: walk from the root with O_NOFOLLOW so no link is ever traversed."""

    flags = os.O_RDONLY | _O_CLOEXEC
    current = os.open(root, flags | _O_DIRECTORY)
    try:
        for component in relative.parts[:-1]:
            try:
                following = os.open(component, flags | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=current)
            except OSError as error:
                msg = f"source ancestor is not a plain directory: {component}"
                raise UnsafeSourceError(msg) from error
            os.close(current)
            current = following
        try:
            # O_NONBLOCK keeps a FIFO swapped in for the file from hanging the
            # open; it has no effect on the regular file we expect.
            descriptor = os.open(
                relative.parts[-1], flags | _O_NOFOLLOW | _O_NONBLOCK, dir_fd=current
            )
        except OSError as error:
            msg = f"source file is not a plain file: {relative.as_posix()}"
            raise UnsafeSourceError(msg) from error
    finally:
        os.close(current)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        msg = f"source file is not a regular file: {relative.as_posix()}"
        raise UnsafeSourceError(msg)
    return descriptor


def _open_by_identity(path: Path, root: Path, relative: Path) -> int:
    """Windows: no openat, so prove the handle is the inspected, confined file."""

    current = root
    for component in relative.parts[:-1]:
        current = current / component
        if _is_link_or_reparse(os.lstat(current)):
            msg = f"source ancestor is a link: {component}"
            raise UnsafeSourceError(msg)
    before = os.lstat(path)
    if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode):
        msg = f"source file is not a plain file: {relative.as_posix()}"
        raise UnsafeSourceError(msg)
    descriptor = os.open(path, os.O_RDONLY | _O_BINARY | _O_NOINHERIT)
    try:
        after = os.fstat(descriptor)
        same_file = (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        if not same_file or not stat.S_ISREG(after.st_mode):
            msg = f"source file changed while it was opened: {relative.as_posix()}"
            raise UnsafeSourceError(msg)
        final = _final_path(descriptor)
        if final is not None and not _is_below(final, root):
            msg = f"source file resolves outside its root: {relative.as_posix()}"
            raise UnsafeSourceError(msg)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_reparse_tag", 0))


def _final_path(descriptor: int) -> str | None:
    """Return the path Windows resolved for an open handle, junctions included."""

    if os.name != "nt":
        return None
    msvcrt = cast("Any", importlib.import_module("msvcrt"))
    kernel32 = cast("Any", ctypes).windll.kernel32
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetFinalPathNameByHandleW(
        ctypes.c_void_p(msvcrt.get_osfhandle(descriptor)), buffer, len(buffer), 0
    )
    if length == 0 or length >= len(buffer):
        return None
    return strip_extended_prefix(buffer.value)


def strip_extended_prefix(final: str) -> str:
    r"""Turn ``\\\\?\\C:\\x`` and ``\\\\?\\UNC\\h\\s`` into ordinary Windows paths."""

    if final.startswith(_EXTENDED_UNC):
        return "\\\\" + final[len(_EXTENDED_UNC) :]
    if final.startswith(_EXTENDED):
        return final[len(_EXTENDED) :]
    return final


def _is_below(candidate: str, root: Path) -> bool:
    # realpath resolves the root through the same final-path API, so both sides
    # name the physical location even when the user's root path uses a junction.
    boundary = os.path.normcase(os.path.realpath(root)).rstrip("\\/")
    return os.path.normcase(candidate).startswith(boundary + os.sep)
