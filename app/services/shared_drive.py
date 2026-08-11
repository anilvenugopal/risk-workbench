"""Live, read-only browse of the mounted broker shared drive (FR-008/FR-009/R11).

Browsing is a **live directory listing** under ``SHARED_DRIVE_ROOT`` — there is no
cached/scanned inventory to reconcile. The chosen path string is stored verbatim on
``irp_edm/irp_rdm.source_file_path``. The app never writes, moves, or deletes on the
drive; these functions only read and validate.

Every resolved path is confined to the root: symlinks and ``..`` that escape the
root are rejected with ``InvalidSourceFile`` (path-traversal guard).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import settings
from app.services.errors import InvalidSourceFile


@dataclass(frozen=True)
class DirEntry:
    name: str
    path: str          # canonical absolute path (what gets stored on selection)
    is_dir: bool
    size: int | None   # bytes for files; None for directories


@dataclass(frozen=True)
class DirListing:
    path: str                 # canonical path of the listed directory
    parent: str | None        # parent within the root, or None at the root
    entries: list[DirEntry]   # directories first, then files, each name-sorted


def _root() -> str:
    root = (settings.shared_drive_root or "").strip()
    if not root:
        raise InvalidSourceFile("No shared drive is configured (SHARED_DRIVE_ROOT).")
    return os.path.realpath(root)


def _resolve_within_root(path: str | None) -> str:
    """Resolve ``path`` (default = root) to a canonical path and confirm it lies
    within the root. Rejects traversal / symlink escapes with ``InvalidSourceFile``."""
    root = _root()
    candidate = root if not path else path
    resolved = os.path.realpath(candidate)
    # Confine to root: equal to root, or a descendant (guard against prefix
    # look-alikes like /srv/rootX by comparing on the path separator boundary).
    if resolved != root and not resolved.startswith(root + os.sep):
        raise InvalidSourceFile(f"Path is outside the shared drive root: {path!r}")
    return resolved


def browse(path: str | None = None) -> DirListing:
    """Live listing of a directory under the root. ``path=None`` (or a submission's
    ``directory_path``) seeds the start at the root. Raises ``InvalidSourceFile`` for
    a path outside the root or one that is not a directory."""
    root = _root()
    resolved = _resolve_within_root(path)
    if not os.path.isdir(resolved):
        raise InvalidSourceFile(f"Not a directory: {path!r}")

    dirs: list[DirEntry] = []
    files: list[DirEntry] = []
    with os.scandir(resolved) as it:
        for entry in it:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            full = os.path.join(resolved, entry.name)
            if is_dir:
                dirs.append(DirEntry(name=entry.name, path=full, is_dir=True, size=None))
            elif entry.is_file(follow_symlinks=False):
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    size = None
                files.append(DirEntry(name=entry.name, path=full, is_dir=False,
                                      size=size))
    dirs.sort(key=lambda e: e.name.lower())
    files.sort(key=lambda e: e.name.lower())

    parent = None if resolved == root else os.path.dirname(resolved)
    return DirListing(path=resolved, parent=parent, entries=[*dirs, *files])


def validate_selection(path: str) -> str:
    """Confirm ``path`` is within the root and is a file; return the canonical path
    to store on the member. Raises ``InvalidSourceFile`` otherwise."""
    resolved = _resolve_within_root(path)
    if not os.path.isfile(resolved):
        raise InvalidSourceFile(f"Selection is not a file: {path!r}")
    return resolved


def validate_directory(path: str) -> str:
    """Confirm ``path`` is within the root and is a directory; return the canonical
    path to store on the submission. Raises ``InvalidSourceFile`` otherwise."""
    resolved = _resolve_within_root(path)
    if not os.path.isdir(resolved):
        raise InvalidSourceFile(f"Not a directory: {path!r}")
    return resolved


__all__ = ["DirEntry", "DirListing", "browse", "validate_directory",
           "validate_selection"]
