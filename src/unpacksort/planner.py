"""Deterministic deduplication and portable path planning."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, Self

from unpacksort.models import Status
from unpacksort.policy import Policy
from unpacksort.safety import (
    bounded_relative_path,
    collision_key,
    safe_component,
    suffixed_name,
)


def freeze_plan(records: list[dict[str, Any]], policy: Policy) -> list[dict[str, Any]]:
    """Compatibility wrapper returning a complete plan for library callers."""

    ordered = sorted(
        (dict(record) for record in records),
        key=lambda record: (
            tuple(str(value) for value in record["sort_key"]),
            str(record["occurrence_id"]),
        ),
    )
    return list(iter_freeze_plan(ordered, policy))


def iter_freeze_plan(
    records: Iterable[dict[str, Any]],
    policy: Policy,
) -> Iterator[dict[str, Any]]:
    """Assign a deterministic plan while keeping collision state on disk.

    The input must already be in portable provenance order. The journal provides
    that order with a cursor, so production planning never retains the complete
    occurrence set or the complete plan in Python memory.
    """

    with _PlannerState() as state:
        for source_record in records:
            record = dict(source_record)
            digest = record.get("digest")
            status = str(record["status"])
            publishable = bool(digest) and (
                status == Status.ELIGIBLE.value
                or (status == Status.UNPROCESSED.value and not policy.pdf_only)
            )
            if not publishable:
                record["canonical_occurrence_id"] = None
                record["canonical_path"] = None
                yield record
                continue
            if not isinstance(digest, str):
                msg = "publishable occurrence is missing a string digest"
                raise TypeError(msg)
            prior = state.canonical(digest)
            if prior is not None:
                record["status"] = Status.DUPLICATE.value
                record["canonical_occurrence_id"] = prior[0]
                record["canonical_path"] = prior[1]
                yield record
                continue
            relative_path = _assign_path(record, policy, state, digest)
            occurrence_id = str(record["occurrence_id"])
            record["canonical_occurrence_id"] = occurrence_id
            record["canonical_path"] = relative_path
            if status == Status.ELIGIBLE.value:
                record["status"] = Status.PUBLISHED.value
            state.record_canonical(digest, occurrence_id, relative_path)
            yield record


class _PlannerState:
    """Disk-backed canonical, collision, and directory assignment indexes."""

    def __init__(self) -> None:
        descriptor, self._path = tempfile.mkstemp(
            prefix="unpacksort-plan-",
            suffix=".sqlite",
        )
        os.close(descriptor)
        self._connection = sqlite3.connect(self._path)
        self._connection.executescript(
            """
            CREATE TABLE canonical (
                digest TEXT PRIMARY KEY,
                occurrence_id TEXT NOT NULL,
                canonical_path TEXT
            );
            CREATE TABLE occupied_files (
                directory_key TEXT NOT NULL,
                name_key TEXT NOT NULL,
                digest TEXT NOT NULL,
                PRIMARY KEY (directory_key, name_key)
            );
            CREATE TABLE directory_assignments (
                parent_key TEXT NOT NULL,
                assignment_key TEXT NOT NULL,
                assigned TEXT NOT NULL,
                PRIMARY KEY (parent_key, assignment_key)
            );
            CREATE TABLE occupied_directories (
                parent_key TEXT NOT NULL,
                assigned_key TEXT NOT NULL,
                PRIMARY KEY (parent_key, assigned_key)
            );
            """
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self._connection.close()
        Path(self._path).unlink(missing_ok=True)

    def canonical(self, digest: str) -> tuple[str, str | None] | None:
        row = self._connection.execute(
            "SELECT occurrence_id, canonical_path FROM canonical WHERE digest = ?",
            (digest,),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), None if row[1] is None else str(row[1])

    def record_canonical(
        self,
        digest: str,
        occurrence_id: str,
        canonical_path: str,
    ) -> None:
        self._connection.execute(
            "INSERT INTO canonical(digest, occurrence_id, canonical_path) VALUES (?, ?, ?)",
            (digest, occurrence_id, canonical_path),
        )

    def file_digest(self, directory_key: str, name_key: str) -> str | None:
        row = self._connection.execute(
            "SELECT digest FROM occupied_files WHERE directory_key = ? AND name_key = ?",
            (directory_key, name_key),
        ).fetchone()
        return None if row is None else str(row[0])

    def occupy_file(self, directory_key: str, name_key: str, digest: str) -> None:
        self._connection.execute(
            "INSERT INTO occupied_files(directory_key, name_key, digest) VALUES (?, ?, ?)",
            (directory_key, name_key, digest),
        )

    def directory_assignment(
        self,
        parent_key: str,
        assignment_key: str,
    ) -> str | None:
        row = self._connection.execute(
            "SELECT assigned FROM directory_assignments "
            "WHERE parent_key = ? AND assignment_key = ?",
            (parent_key, assignment_key),
        ).fetchone()
        return None if row is None else str(row[0])

    def directory_is_occupied(self, parent_key: str, assigned_key: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM occupied_directories WHERE parent_key = ? AND assigned_key = ?",
            (parent_key, assigned_key),
        ).fetchone()
        return row is not None

    def assign_directory(
        self,
        parent_key: str,
        assignment_key: str,
        assigned: str,
    ) -> None:
        self._connection.execute(
            "INSERT INTO occupied_directories(parent_key, assigned_key) VALUES (?, ?)",
            (parent_key, collision_key(assigned)),
        )
        self._connection.execute(
            "INSERT INTO directory_assignments(parent_key, assignment_key, assigned) "
            "VALUES (?, ?, ?)",
            (parent_key, assignment_key, assigned),
        )


def _assign_path(
    record: dict[str, Any],
    policy: Policy,
    state: _PlannerState,
    digest: str,
) -> str:
    group_parts = str(record["detection"]["group"]).split("/")
    requested_name = safe_component(str(record["generated_name"]), fallback="file.bin")[0]
    if policy.layout == "flatten":
        directory_parts = group_parts
    else:
        directory_parts = [*group_parts, str(record["source_root"])]
        for node in record["ancestry"]:
            if node["kind"] == "archive_member_index":
                continue
            raw = node.get("original_name") or node["identifier"]
            for component in PurePosixPath(str(raw).replace("\\", "/")).parts:
                if component not in {"", ".", requested_name}:
                    directory_parts.append(component)
    directory_parts = _portable_directories(
        directory_parts,
        state,
    )
    directory = "/".join(directory_parts)
    directory_key = collision_key(directory)
    requested_key = collision_key(requested_name)
    chosen = requested_name
    suffix = 0
    prior_digest = state.file_digest(directory_key, requested_key)
    while prior_digest is not None and prior_digest != digest:
        suffix += 1
        chosen = suffixed_name(requested_name, suffix)
        requested_key = collision_key(chosen)
        prior_digest = state.file_digest(directory_key, requested_key)
    if suffix:
        metadata = dict(record.get("metadata") or {})
        adjustments = filter(
            None,
            str(metadata.get("path_adjustments", "")).split(","),
        )
        metadata["path_adjustments"] = ",".join(
            dict.fromkeys((*adjustments, "name_collision_suffix")),
        )
        record["metadata"] = metadata
    if prior_digest is None:
        state.occupy_file(directory_key, requested_key, digest)
    return bounded_relative_path(
        [*directory_parts, chosen],
        protected_prefix=len(group_parts),
    )


def _portable_directories(
    parts: list[str],
    state: _PlannerState,
) -> list[str]:
    resolved: list[str] = []
    for part in parts:
        parent_key = collision_key("/".join(resolved))
        safe = safe_component(part)[0]
        identity = part.encode("utf-8", "surrogatepass").hex()
        assignment_key = f"{collision_key(safe)}\0{identity}"
        assigned = state.directory_assignment(parent_key, assignment_key)
        if assigned is None:
            assigned = safe
            suffix = 0
            while state.directory_is_occupied(parent_key, collision_key(assigned)):
                suffix += 1
                assigned = suffixed_name(safe, suffix)
            state.assign_directory(parent_key, assignment_key, assigned)
        resolved.append(assigned)
    return resolved
