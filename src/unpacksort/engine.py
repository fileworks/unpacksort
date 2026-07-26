"""Journal-authoritative discovery, planning, publication, and reporting."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from unpacksort import __version__
from unpacksort.containers import ContainerFailureError, adapter_for
from unpacksort.detection import detect
from unpacksort.journal import Journal, StateConflictError
from unpacksort.mail import (
    MailLeaf,
    is_confirmed_mbox,
    is_confirmed_message,
    iter_mbox,
    iter_message_path,
)
from unpacksort.models import (
    AncestryNode,
    Blob,
    ContainerRecord,
    DetectionMethod,
    DetectionResult,
    Diagnostic,
    ExitOutcome,
    Group,
    Occurrence,
    Reason,
    SourceIdentity,
    Status,
)
from unpacksort.planner import freeze_plan
from unpacksort.policy import Accounting, ContainerAccounting, LimitExceededError, Policy
from unpacksort.reporting import write_outputs
from unpacksort.safety import portable_text_key, safe_component
from unpacksort.storage import BlobStore


class _RecordSink(Protocol):
    def record_occurrence(self, occurrence: Occurrence) -> None:
        """Record one occurrence."""

    def record_container(self, container: ContainerRecord) -> None:
        """Record one container."""


@dataclass(slots=True)
class _RecordBuffer:
    occurrences: list[Occurrence] = field(default_factory=list)
    containers: list[ContainerRecord] = field(default_factory=list)

    def record_occurrence(self, occurrence: Occurrence) -> None:
        self.occurrences.append(occurrence)

    def record_container(self, container: ContainerRecord) -> None:
        self.containers.append(container)

    def flush(self, target: _RecordSink) -> None:
        for container in self.containers:
            target.record_container(container)
        for occurrence in self.occurrences:
            target.record_occurrence(occurrence)


class Processor:
    """Execute one deterministic destination-local run."""

    def __init__(self, source: Path, destination: Path, policy: Policy) -> None:
        """Prepare a run without mutating its destination."""
        self.source = source
        self.destination = destination
        self.policy = policy
        self.accounting = Accounting(policy)
        self.source_identity = fingerprint_source(source)

    def run(self) -> tuple[Path, Path, ExitOutcome]:
        """Discover or resume, freeze, publish, and commit final reports."""

        self.destination.mkdir(parents=True, exist_ok=True)
        with Journal(self.destination) as journal:
            journal.prepare(self.source_identity, self.policy, tool_version=__version__)
            store = BlobStore(journal)
            if journal.phase == "discovery":
                self._discover(journal, store)
                journal.set_accounting(
                    members=self.accounting.members,
                    expanded_bytes=self.accounting.expanded_bytes,
                )
                plan = freeze_plan(journal.occurrence_records(), self.policy)
                journal.freeze(plan)
            else:
                plan = journal.plan_records()
                self.accounting.members, self.accounting.expanded_bytes = journal.accounting()
            self._publish(journal, store, plan)
            manifest, report, outcome = write_outputs(
                self.destination,
                source={
                    "fingerprint": self.source_identity.fingerprint,
                    "kind": self.source_identity.kind,
                    "root_name": self.source_identity.root_name,
                },
                policy=self.policy,
                accounting=self.accounting,
                containers=journal.container_records(),
                plan=plan,
            )
            journal.complete("partial" if outcome == ExitOutcome.PARTIAL else "complete")
            return manifest, report, outcome

    def _discover(self, journal: Journal, store: BlobStore) -> None:
        if self.source.is_file():
            for leaf in iter_mbox(self.source):
                self._process_mail_leaf(
                    leaf,
                    journal,
                    store,
                    source_path=self.source.name,
                    ancestor_digests=frozenset(),
                )
            return
        for path, relative_path, ancestry, unsafe_reason in _walk_directory(self.source):
            if unsafe_reason is not None:
                self._record_failure(
                    journal,
                    source_path=relative_path,
                    logical_name=path.name,
                    ancestry=ancestry,
                    reason=unsafe_reason,
                )
                continue
            try:
                if is_confirmed_mbox(path):
                    for leaf in iter_mbox(path):
                        self._process_mail_leaf(
                            leaf,
                            journal,
                            store,
                            source_path=relative_path,
                            ancestor_digests=frozenset(),
                            source_ancestry=ancestry,
                        )
                    continue
                blob = self._ingest_path(path, store, ())
                self._process_blob(
                    blob,
                    logical_name=path.name,
                    source_path=relative_path,
                    ancestry=ancestry,
                    depth=0,
                    ancestor_digests=frozenset(),
                    journal=journal,
                    store=store,
                    metadata={},
                    diagnostics=(),
                )
            except (OSError, LimitExceededError) as error:
                reason = (
                    error.reason if isinstance(error, LimitExceededError) else Reason.UNREADABLE
                )
                self._record_failure(
                    journal,
                    source_path=relative_path,
                    logical_name=path.name,
                    ancestry=ancestry,
                    reason=reason,
                )

    def _process_mail_leaf(
        self,
        leaf: MailLeaf,
        journal: _RecordSink,
        store: BlobStore,
        *,
        source_path: str,
        ancestor_digests: frozenset[str],
        source_ancestry: tuple[AncestryNode, ...] = (),
        budgets: tuple[ContainerAccounting, ...] = (),
    ) -> None:
        ancestry = (*source_ancestry, *leaf.ancestry)
        depth = sum(node.kind in {"message", "archive"} for node in ancestry)
        if depth > self.policy.max_depth:
            self._record_failure(
                journal,
                source_path=source_path,
                logical_name=leaf.logical_name,
                ancestry=ancestry,
                reason=Reason.DEPTH_LIMIT,
                diagnostics=leaf.diagnostics,
                metadata=leaf.metadata,
            )
            return
        if leaf.payload is None:
            self._record_failure(
                journal,
                source_path=source_path,
                logical_name=leaf.logical_name,
                ancestry=ancestry,
                reason=leaf.reason or Reason.UNREADABLE,
                diagnostics=leaf.diagnostics,
                metadata=leaf.metadata,
            )
            return
        try:
            blob = self._ingest_bytes(leaf.payload, store, budgets)
        except LimitExceededError as error:
            if budgets:
                owner = error.owner or budgets[-1].identifier
                raise ContainerFailureError(error.reason, owner=owner) from error
            self._record_failure(
                journal,
                source_path=source_path,
                logical_name=leaf.logical_name,
                ancestry=ancestry,
                reason=error.reason,
                diagnostics=leaf.diagnostics,
                metadata=leaf.metadata,
            )
            return
        self._process_blob(
            blob,
            logical_name=leaf.logical_name,
            source_path=source_path,
            ancestry=ancestry,
            depth=depth,
            ancestor_digests=ancestor_digests,
            journal=journal,
            store=store,
            metadata=leaf.metadata,
            diagnostics=leaf.diagnostics,
            force_atomic=leaf.attached_message,
            budgets=budgets,
        )

    def _process_blob(
        self,
        blob: Blob,
        *,
        logical_name: str,
        source_path: str,
        ancestry: tuple[AncestryNode, ...],
        depth: int,
        ancestor_digests: frozenset[str],
        journal: _RecordSink,
        store: BlobStore,
        metadata: dict[str, str],
        diagnostics: tuple[Diagnostic, ...],
        force_atomic: bool = False,
        budgets: tuple[ContainerAccounting, ...] = (),
    ) -> None:
        if not force_atomic and (is_confirmed_mbox(blob.path) or is_confirmed_message(blob.path)):
            self._process_mail_container(
                blob,
                logical_name=logical_name,
                source_path=source_path,
                ancestry=ancestry,
                depth=depth,
                ancestor_digests=ancestor_digests,
                journal=journal,
                store=store,
                metadata=metadata,
                diagnostics=diagnostics,
                budgets=budgets,
            )
            return
        detection = detect(blob.path, logical_name)
        if force_atomic:
            detection = DetectionResult(
                "message/rfc822",
                Group.EMAIL,
                ".eml",
                DetectionMethod.PARSER,
            )
        if detection.reason is not None:
            self._record_blob_occurrence(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                Status.UNPROCESSED,
                detection.reason,
                diagnostics,
                metadata,
            )
            return
        if detection.container is None or force_atomic:
            status = (
                Status.SKIPPED
                if self.policy.pdf_only and detection.group is not Group.PDF
                else Status.ELIGIBLE
            )
            reason = Reason.POLICY_NON_PDF if status is Status.SKIPPED else None
            self._record_blob_occurrence(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                status,
                reason,
                diagnostics,
                metadata,
            )
            return
        container_id = _stable_id(
            "container",
            *self._sort_key(source_path, ancestry, logical_name),
            blob.digest,
        )
        if depth >= self.policy.max_depth:
            self._container_failure(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                container_id,
                Reason.DEPTH_LIMIT,
            )
            return
        if blob.digest in ancestor_digests:
            self._container_failure(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                container_id,
                Reason.CYCLE,
            )
            return
        try:
            container_budget = ContainerAccounting(self.policy, container_id)
            members = adapter_for(detection.container).members(
                blob,
                store,
                self.policy,
                self.accounting,
                (*budgets, container_budget),
            )
        except ContainerFailureError as error:
            if error.owner is not None and error.owner != container_id:
                raise
            self._container_failure(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                container_id,
                error.reason,
            )
            return
        archive_node = AncestryNode("archive", logical_name, logical_name)
        subtree = _RecordBuffer()
        try:
            for member in members:
                member_path = PurePosixPath(member.name.replace("\\", "/"))
                parent_nodes = tuple(
                    AncestryNode("archive_path", component, component)
                    for component in member_path.parent.parts
                    if component not in {"", "."}
                )
                member_index = AncestryNode(
                    "archive_member_index",
                    f"member-{member.index:06d}",
                )
                self._process_blob(
                    member.blob,
                    logical_name=member_path.name,
                    source_path=source_path,
                    ancestry=(*ancestry, archive_node, *parent_nodes, member_index),
                    depth=depth + 1,
                    ancestor_digests=ancestor_digests | {blob.digest},
                    journal=subtree,
                    store=store,
                    metadata={
                        "archive_member_name": member.name,
                        "archive_member_index": str(member.index),
                        "archive_declared_size": str(member.declared_size),
                        "archive_compressed_size": (
                            "" if member.compressed_size is None else str(member.compressed_size)
                        ),
                    },
                    diagnostics=(),
                    budgets=(*budgets, container_budget),
                )
        except ContainerFailureError as error:
            if error.owner != container_id:
                raise
            self._container_failure(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                container_id,
                error.reason,
            )
            return
        subtree.record_container(
            ContainerRecord(
                container_id=container_id,
                sort_key=self._sort_key(source_path, ancestry, logical_name),
                kind=detection.container,
                digest=blob.digest,
                source_path=source_path,
                ancestry=ancestry,
                status="expanded",
                member_count=len(members),
                expanded_bytes=container_budget.expanded_bytes,
            )
        )
        subtree.flush(journal)

    def _process_mail_container(
        self,
        blob: Blob,
        *,
        logical_name: str,
        source_path: str,
        ancestry: tuple[AncestryNode, ...],
        depth: int,
        ancestor_digests: frozenset[str],
        journal: _RecordSink,
        store: BlobStore,
        metadata: dict[str, str],
        diagnostics: tuple[Diagnostic, ...],
        budgets: tuple[ContainerAccounting, ...],
    ) -> None:
        is_mbox = is_confirmed_mbox(blob.path)
        kind = "mbox" if is_mbox else "message"
        detection = DetectionResult(
            "application/mbox" if is_mbox else "message/rfc822",
            Group.EMAIL,
            ".mbox" if is_mbox else ".eml",
            DetectionMethod.PARSER,
            container=kind,
        )
        container_id = _stable_id(
            "container",
            *self._sort_key(source_path, ancestry, logical_name),
            blob.digest,
        )
        if depth >= self.policy.max_depth:
            self._container_failure(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                container_id,
                Reason.DEPTH_LIMIT,
            )
            return
        if blob.digest in ancestor_digests:
            self._container_failure(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                container_id,
                Reason.CYCLE,
            )
            return
        container_budget = ContainerAccounting(self.policy, container_id)
        active_budgets = (*budgets, container_budget)
        subtree = _RecordBuffer()
        leaf_count = 0
        try:
            if is_mbox:
                leaves = iter_mbox(blob.path)
            else:
                root = (*ancestry, AncestryNode("message", "message-000001", logical_name))
                leaves = iter_message_path(blob.path, root, blob.digest)
            for leaf_count, leaf in enumerate(leaves, start=1):
                if leaf_count > self.policy.max_members_per_container:
                    raise ContainerFailureError(
                        Reason.MEMBER_COUNT_LIMIT,
                        owner=container_id,
                    )
                enriched = replace(
                    leaf,
                    metadata={**metadata, **leaf.metadata},
                    diagnostics=(*diagnostics, *leaf.diagnostics),
                )
                self._process_mail_leaf(
                    enriched,
                    subtree,
                    store,
                    source_path=source_path,
                    ancestor_digests=ancestor_digests | {blob.digest},
                    source_ancestry=ancestry if is_mbox else (),
                    budgets=active_budgets,
                )
        except ContainerFailureError as error:
            if error.owner != container_id:
                raise
            self._container_failure(
                journal,
                blob,
                logical_name,
                source_path,
                ancestry,
                detection,
                container_id,
                error.reason,
            )
            return
        subtree.record_container(
            ContainerRecord(
                container_id=container_id,
                sort_key=self._sort_key(source_path, ancestry, logical_name),
                kind=kind,
                digest=blob.digest,
                source_path=source_path,
                ancestry=ancestry,
                status="expanded",
                member_count=leaf_count,
                expanded_bytes=container_budget.expanded_bytes,
            ),
        )
        subtree.flush(journal)

    def _container_failure(
        self,
        journal: _RecordSink,
        blob: Blob,
        logical_name: str,
        source_path: str,
        ancestry: tuple[AncestryNode, ...],
        detection: DetectionResult,
        container_id: str,
        reason: Reason,
    ) -> None:
        failed_detection = replace(
            detection,
            group=Group.ARCHIVES_UNPROCESSED,
            reason=reason,
        )
        journal.record_container(
            ContainerRecord(
                container_id=container_id,
                sort_key=self._sort_key(source_path, ancestry, logical_name),
                kind=detection.container or "unknown",
                digest=blob.digest,
                source_path=source_path,
                ancestry=ancestry,
                status="unprocessed",
                reason=reason,
            ),
        )
        self._record_blob_occurrence(
            journal,
            blob,
            logical_name,
            source_path,
            ancestry,
            failed_detection,
            Status.UNPROCESSED,
            reason,
            (),
            {},
        )

    def _record_blob_occurrence(
        self,
        journal: _RecordSink,
        blob: Blob,
        logical_name: str,
        source_path: str,
        ancestry: tuple[AncestryNode, ...],
        detection: DetectionResult,
        status: Status,
        reason: Reason | None,
        diagnostics: tuple[Diagnostic, ...],
        metadata: dict[str, str],
    ) -> None:
        generated_name, normalization_reasons = _canonical_name(
            logical_name,
            detection.extension,
        )
        occurrence_metadata = dict(metadata)
        if normalization_reasons:
            occurrence_metadata["name_normalization_reasons"] = ",".join(
                normalization_reasons,
            )
        sort_key = self._sort_key(source_path, ancestry, logical_name)
        occurrence_id = _stable_id("occurrence", *sort_key)
        journal.record_occurrence(
            Occurrence(
                occurrence_id=occurrence_id,
                sort_key=sort_key,
                source_path=source_path,
                source_root=self.source_identity.root_name,
                ancestry=ancestry,
                original_name=logical_name,
                generated_name=generated_name,
                detection=detection,
                status=status,
                digest=blob.digest,
                size=blob.size,
                reason=reason,
                diagnostics=diagnostics,
                metadata=occurrence_metadata,
            ),
        )

    def _record_failure(
        self,
        journal: _RecordSink,
        *,
        source_path: str,
        logical_name: str,
        ancestry: tuple[AncestryNode, ...],
        reason: Reason,
        diagnostics: tuple[Diagnostic, ...] = (),
        metadata: dict[str, str] | None = None,
    ) -> None:
        detection = DetectionResult(
            "application/octet-stream",
            Group.ARCHIVES_UNPROCESSED,
            PurePosixPath(logical_name).suffix or ".bin",
            DetectionMethod.UNKNOWN,
            reason=reason,
        )
        sort_key = self._sort_key(source_path, ancestry, logical_name)
        generated_name, normalization_reasons = _canonical_name(
            logical_name,
            detection.extension,
        )
        occurrence_metadata = dict(metadata or {})
        if normalization_reasons:
            occurrence_metadata["name_normalization_reasons"] = ",".join(
                normalization_reasons,
            )
        journal.record_occurrence(
            Occurrence(
                occurrence_id=_stable_id("occurrence", *sort_key, reason.value),
                sort_key=sort_key,
                source_path=source_path,
                source_root=self.source_identity.root_name,
                ancestry=ancestry,
                original_name=logical_name,
                generated_name=generated_name,
                detection=detection,
                status=Status.UNPROCESSED,
                reason=reason,
                diagnostics=diagnostics,
                metadata=occurrence_metadata,
            ),
        )

    def _ingest_path(
        self,
        path: Path,
        store: BlobStore,
        budgets: tuple[ContainerAccounting, ...],
    ) -> Blob:
        size = path.stat(follow_symlinks=False).st_size
        self._preflight_payload(size, budgets)
        limit, reason, owner = self._payload_bound(budgets)
        blob = store.ingest_path(
            path,
            max_bytes=limit,
            limit_reason=reason,
            limit_owner=owner,
        )
        self._charge_payload(blob.size, budgets)
        return blob

    def _ingest_bytes(
        self,
        payload: bytes,
        store: BlobStore,
        budgets: tuple[ContainerAccounting, ...],
    ) -> Blob:
        self._preflight_payload(len(payload), budgets)
        limit, reason, owner = self._payload_bound(budgets)
        blob = store.ingest_bytes(
            payload,
            max_bytes=limit,
            limit_reason=reason,
            limit_owner=owner,
        )
        self._charge_payload(blob.size, budgets)
        return blob

    def _preflight_payload(
        self,
        size: int,
        budgets: tuple[ContainerAccounting, ...],
    ) -> None:
        if size > self.policy.max_member_bytes:
            raise LimitExceededError(Reason.MEMBER_SIZE_LIMIT)
        self.accounting.preflight(members=1, expanded_bytes=size)
        for budget in budgets:
            budget.preflight(size)

    def _payload_bound(
        self,
        budgets: tuple[ContainerAccounting, ...],
    ) -> tuple[int, Reason, str | None]:
        candidates = [
            (self.policy.max_member_bytes, Reason.MEMBER_SIZE_LIMIT, None),
            (self.accounting.remaining_bytes, Reason.GLOBAL_SIZE_LIMIT, None),
            *[
                (budget.remaining_bytes, Reason.CONTAINER_SIZE_LIMIT, budget.identifier)
                for budget in budgets
            ],
        ]
        return min(candidates, key=lambda candidate: candidate[0])

    def _charge_payload(
        self,
        size: int,
        budgets: tuple[ContainerAccounting, ...],
    ) -> None:
        self.accounting.charge(size)
        for budget in budgets:
            budget.charge(size)

    @staticmethod
    def _sort_key(
        source_path: str,
        ancestry: tuple[AncestryNode, ...],
        logical_name: str,
    ) -> tuple[str, ...]:
        values = [source_path]
        values.extend(f"{node.kind}:{node.identifier}" for node in ancestry)
        values.append(logical_name)
        key: list[str] = []
        for value in values:
            folded, raw = portable_text_key(value)
            key.extend((folded, raw.hex()))
        return tuple(key)

    def _publish(
        self,
        journal: Journal,
        store: BlobStore,
        plan: list[dict[str, Any]],
    ) -> None:
        for record in plan:
            if record.get("canonical_occurrence_id") != record["occurrence_id"]:
                continue
            relative = record.get("canonical_path")
            digest = record.get("digest")
            size = record.get("size")
            if not relative or not digest or size is None:
                continue
            destination = self.destination / str(relative)
            blob_path = store.root / str(digest)[:2] / str(digest)
            if destination.exists():
                if _sha256(destination) != digest:
                    msg = f"planned destination was replaced: {relative}"
                    raise StateConflictError(msg)
            else:
                store.publish(Blob(str(digest), int(size), blob_path), destination)
            journal.mark_published(str(record["occurrence_id"]))


def fingerprint_source(path: Path) -> SourceIdentity:
    """Hash deterministic source identities and content before journal reuse."""

    digest = hashlib.sha256()
    if path.is_file():
        digest.update(b"mbox\0")
        digest.update(path.name.encode("utf-8", "surrogatepass"))
        _update_file_digest(digest, path)
        return SourceIdentity("mbox", path.name, digest.hexdigest())
    digest.update(b"directory\0")
    for candidate, relative, _ancestry, unsafe_reason in _walk_directory(path):
        digest.update(relative.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
        if unsafe_reason is not None:
            digest.update(unsafe_reason.value.encode())
        else:
            try:
                _update_file_digest(digest, candidate)
            except OSError:
                digest.update(Reason.UNREADABLE.value.encode())
                try:
                    metadata = candidate.stat(follow_symlinks=False)
                except OSError:
                    pass
                else:
                    digest.update(f"{metadata.st_size}:{metadata.st_mtime_ns}".encode())
    return SourceIdentity("directory", path.name, digest.hexdigest())


def _walk_directory(
    root: Path,
) -> list[tuple[Path, str, tuple[AncestryNode, ...], Reason | None]]:
    found: list[tuple[Path, str, tuple[AncestryNode, ...], Reason | None]] = []

    def visit(directory: Path, relative_parent: PurePosixPath) -> None:
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(
                    scanner,
                    key=lambda entry: (
                        *portable_text_key(entry.name),
                        entry.name.encode("utf-8", "surrogatepass").hex(),
                    ),
                )
        except OSError:
            relative_text = relative_parent.as_posix() or "."
            ancestry = tuple(
                AncestryNode("source_dir", component, component)
                for component in relative_parent.parent.parts
                if component not in {"", "."}
            )
            found.append((directory, relative_text, ancestry, Reason.UNREADABLE))
            return
        for entry in entries:
            relative = relative_parent / entry.name
            relative_text = relative.as_posix()
            ancestry = tuple(
                AncestryNode("source_dir", component, component)
                for component in relative.parent.parts
                if component not in {"", "."}
            )
            try:
                entry_stat = entry.stat(follow_symlinks=False)
                mode = entry_stat.st_mode
            except OSError:
                found.append((Path(entry.path), relative_text, ancestry, Reason.UNREADABLE))
                continue
            if (
                stat.S_ISLNK(mode)
                or bool(getattr(entry_stat, "st_reparse_tag", 0))
                or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))
            ):
                found.append((Path(entry.path), relative_text, ancestry, Reason.UNSAFE_ENTRY))
            elif stat.S_ISDIR(mode):
                visit(Path(entry.path), relative)
            else:
                found.append((Path(entry.path), relative_text, ancestry, None))

    visit(root, PurePosixPath())
    return found


def _update_file_digest(digest: DigestWriter, path: Path) -> None:
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)


def _stable_id(prefix: str, *values: str) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


def _canonical_name(name: str, extension: str) -> tuple[str, tuple[str, ...]]:
    normalized, initial_reasons = safe_component(name, fallback="file")
    suffix = PurePosixPath(normalized).suffix
    stem = normalized[: -len(suffix)] if suffix else normalized
    generated, final_reasons = safe_component(
        f"{stem}{extension}",
        fallback=f"file{extension}",
    )
    return generated, tuple(dict.fromkeys((*initial_reasons, *final_reasons)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    _update_file_digest(digest, path)
    return digest.hexdigest()


class DigestWriter(Protocol):
    """Narrow structural type shared by hashlib digest objects."""

    def update(self, payload: bytes) -> None:
        """Add bytes to the digest."""
