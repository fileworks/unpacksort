"""Versioned SQLite journal for discovery, planning, and publication."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from itertools import zip_longest
from pathlib import Path
from typing import Any, Self

from unpacksort.models import Blob, ContainerRecord, Occurrence, SourceIdentity
from unpacksort.policy import Policy

SCHEMA_VERSION = 1


class StateConflictError(Exception):
    """The destination contains an incompatible journal or frozen plan."""


class Journal:
    """Transactional destination-local journal."""

    def __init__(self, destination: Path) -> None:
        """Open or create the destination-local journal."""
        self.state_dir = destination / ".unpacksort"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "journal.sqlite"
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        try:
            self._initialize()
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        """Close the journal connection."""

        self._connection.close()

    def __enter__(self) -> Self:
        """Return the open journal context."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the journal context."""
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one immediate transaction with rollback on failure."""

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _initialize(self) -> None:
        current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if current not in {0, SCHEMA_VERSION}:
            msg = f"journal schema {current} is incompatible with {SCHEMA_VERSION}"
            raise StateConflictError(msg)
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_identities (
                    fingerprint TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    committed INTEGER NOT NULL CHECK (committed IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS blobs (
                    digest TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    committed INTEGER NOT NULL CHECK (committed IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS occurrences (
                    occurrence_id TEXT PRIMARY KEY,
                    sort_key TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS diagnostics (
                    diagnostic_id TEXT PRIMARY KEY,
                    occurrence_id TEXT,
                    code TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS containers (
                    container_id TEXT PRIMARY KEY,
                    sort_key TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS frozen_plans (
                    occurrence_id TEXT PRIMARY KEY,
                    ordinal INTEGER NOT NULL UNIQUE,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publication_states (
                    occurrence_id TEXT PRIMARY KEY,
                    relative_path TEXT,
                    state TEXT NOT NULL
                );
                """,
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _get(self, key: str) -> str | None:
        row = self._connection.execute("SELECT value FROM runs WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def _set(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO runs(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    @property
    def phase(self) -> str:
        """Return the current two-phase state."""

        return self._get("phase") or "discovery"

    def prepare(self, source: SourceIdentity, policy: Policy, *, tool_version: str) -> None:
        """Create or validate compatibility metadata."""

        expected = {
            "source_fingerprint": source.fingerprint,
            "source_kind": source.kind,
            "source_root": source.root_name,
            "policy_fingerprint": policy.fingerprint(),
            "tool_version": tool_version,
            "schema_version": str(SCHEMA_VERSION),
        }
        with self.transaction() as connection:
            del connection
            for key, value in expected.items():
                current = self._get(key)
                if current is not None and current != value:
                    msg = f"destination journal conflict: {key} changed"
                    raise StateConflictError(msg)
                self._set(key, value)
            self._set("phase", self._get("phase") or "discovery")
            payload = json.dumps(
                {
                    "kind": source.kind,
                    "root_name": source.root_name,
                    "fingerprint": source.fingerprint,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO source_identities(fingerprint, payload) VALUES (?, ?)",
                (source.fingerprint, payload),
            )

    def record_blob(self, blob: Blob) -> None:
        """Commit a complete staged blob."""

        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO blobs(digest, size, path, committed) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(digest) DO UPDATE SET "
                "size = excluded.size, path = excluded.path, committed = 1",
                (blob.digest, blob.size, str(blob.path)),
            )

    def record_occurrence(self, occurrence: Occurrence) -> None:
        """Upsert a deterministic occurrence and its diagnostics."""

        self.record_occurrence_record(occurrence.to_record())

    def record_occurrence_record(self, record: dict[str, Any]) -> None:
        """Upsert one already serialized occurrence record."""

        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
        sort_key = json.dumps(record["sort_key"], ensure_ascii=False, separators=(",", ":"))
        occurrence_id = str(record["occurrence_id"])
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO occurrences(occurrence_id, sort_key, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(occurrence_id) DO UPDATE SET "
                "sort_key = excluded.sort_key, payload = excluded.payload",
                (occurrence_id, sort_key, payload),
            )
            connection.execute(
                "INSERT INTO nodes(node_id, payload, committed) VALUES (?, ?, 1) "
                "ON CONFLICT(node_id) DO UPDATE SET "
                "payload = excluded.payload, committed = 1",
                (occurrence_id, payload),
            )
            connection.execute(
                "DELETE FROM diagnostics WHERE occurrence_id = ?",
                (occurrence_id,),
            )
            diagnostics = record.get("diagnostics") or []
            for index, diagnostic in enumerate(diagnostics):
                connection.execute(
                    "INSERT INTO diagnostics("
                    "diagnostic_id, occurrence_id, code, detail"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        f"{occurrence_id}:{index:04d}",
                        occurrence_id,
                        str(diagnostic["code"]),
                        str(diagnostic.get("detail") or ""),
                    ),
                )

    def record_container(self, container: ContainerRecord) -> None:
        """Upsert a deterministic container record."""

        self.record_container_record(container.to_record())

    def record_container_record(self, record: dict[str, Any]) -> None:
        """Upsert one already serialized container record."""

        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
        sort_key = json.dumps(record["sort_key"], ensure_ascii=False, separators=(",", ":"))
        container_id = str(record["container_id"])
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO containers(container_id, sort_key, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(container_id) DO UPDATE SET "
                "sort_key = excluded.sort_key, payload = excluded.payload",
                (container_id, sort_key, payload),
            )
            connection.execute(
                "INSERT INTO nodes(node_id, payload, committed) VALUES (?, ?, 1) "
                "ON CONFLICT(node_id) DO UPDATE SET "
                "payload = excluded.payload, committed = 1",
                (container_id, payload),
            )

    def occurrence_records(self) -> list[dict[str, Any]]:
        """Compatibility wrapper loading occurrences into a list."""

        return list(self.iter_occurrence_records())

    def iter_occurrence_records(self) -> Iterator[dict[str, Any]]:
        """Stream discovered occurrences in portable provenance order."""
        rows = self._connection.execute(
            "SELECT payload FROM occurrences ORDER BY sort_key, occurrence_id",
        )
        for row in rows:
            yield json.loads(str(row["payload"]))

    def container_records(self) -> list[dict[str, Any]]:
        """Compatibility wrapper loading containers into a list."""

        return list(self.iter_container_records())

    def iter_container_records(self) -> Iterator[dict[str, Any]]:
        """Stream containers in portable provenance order."""
        rows = self._connection.execute(
            "SELECT payload FROM containers ORDER BY sort_key, container_id",
        )
        for row in rows:
            yield json.loads(str(row["payload"]))

    def freeze(self, plan: Iterable[dict[str, Any]]) -> None:
        """Persist the immutable path plan once."""

        with self.transaction() as connection:
            existing = connection.execute("SELECT COUNT(*) FROM frozen_plans").fetchone()[0]
            if existing:
                missing = object()
                for stored, candidate in zip_longest(
                    self.iter_plan_records(),
                    plan,
                    fillvalue=missing,
                ):
                    if stored != candidate:
                        msg = "destination already contains a different frozen plan"
                        raise StateConflictError(msg)
                return
            for ordinal, record in enumerate(plan):
                connection.execute(
                    "INSERT INTO frozen_plans(occurrence_id, ordinal, payload) VALUES (?, ?, ?)",
                    (
                        record["occurrence_id"],
                        ordinal,
                        json.dumps(record, sort_keys=True, separators=(",", ":")),
                    ),
                )
                connection.execute(
                    "INSERT INTO publication_states("
                    "occurrence_id, relative_path, state"
                    ") VALUES (?, ?, 'planned')",
                    (record["occurrence_id"], record.get("canonical_path")),
                )
            self._set("phase", "frozen")

    def set_accounting(self, *, members: int, expanded_bytes: int) -> None:
        """Persist deterministic logical safety counters for resume."""
        with self.transaction():
            self._set("accounting_members", str(members))
            self._set("accounting_expanded_bytes", str(expanded_bytes))

    def accounting(self) -> tuple[int, int]:
        """Load deterministic logical safety counters."""
        return (
            int(self._get("accounting_members") or "0"),
            int(self._get("accounting_expanded_bytes") or "0"),
        )

    def plan_records(self) -> list[dict[str, Any]]:
        """Compatibility wrapper loading the immutable plan into a list."""

        return list(self.iter_plan_records())

    def iter_plan_records(self) -> Iterator[dict[str, Any]]:
        """Stream the immutable plan in ordinal order."""
        rows = self._connection.execute(
            "SELECT payload FROM frozen_plans ORDER BY ordinal",
        )
        for row in rows:
            yield json.loads(str(row["payload"]))

    def plan_count(self) -> int:
        """Return the immutable plan size without materializing it."""

        return int(self._connection.execute("SELECT COUNT(*) FROM frozen_plans").fetchone()[0])

    def mark_published(self, occurrence_id: str) -> None:
        """Commit one atomic publication transition."""

        with self.transaction() as connection:
            connection.execute(
                "UPDATE publication_states SET state = 'published' WHERE occurrence_id = ?",
                (occurrence_id,),
            )

    def complete(self, outcome: str) -> None:
        """Mark final deterministic outputs as committed."""

        with self.transaction():
            self._set("outcome", outcome)
            self._set("phase", "complete")
