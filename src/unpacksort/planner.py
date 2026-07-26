"""Deterministic deduplication and portable path planning."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from unpacksort.models import Status
from unpacksort.policy import Policy
from unpacksort.safety import (
    bounded_relative_path,
    collision_key,
    safe_component,
    suffixed_name,
)


def freeze_plan(records: list[dict[str, Any]], policy: Policy) -> list[dict[str, Any]]:
    """Select canonical occurrences and assign immutable portable paths."""

    ordered = sorted(
        (dict(record) for record in records),
        key=lambda record: (
            tuple(str(value) for value in record["sort_key"]),
            str(record["occurrence_id"]),
        ),
    )
    canonical_by_digest: dict[str, tuple[str, str | None]] = {}
    occupied: dict[str, dict[str, str]] = {}
    directory_assignments: dict[str, dict[str, str]] = {}
    directory_occupied: dict[str, set[str]] = {}
    plan: list[dict[str, Any]] = []
    for record in ordered:
        digest = record.get("digest")
        status = str(record["status"])
        publishable = bool(digest) and (
            status == Status.ELIGIBLE.value
            or (status == Status.UNPROCESSED.value and not policy.pdf_only)
        )
        if not publishable:
            record["canonical_occurrence_id"] = None
            record["canonical_path"] = None
            plan.append(record)
            continue
        if not isinstance(digest, str):
            msg = "publishable occurrence is missing a string digest"
            raise TypeError(msg)
        prior = canonical_by_digest.get(digest)
        if prior is not None:
            record["status"] = Status.DUPLICATE.value
            record["canonical_occurrence_id"] = prior[0]
            record["canonical_path"] = prior[1]
            plan.append(record)
            continue
        relative_path = _assign_path(
            record,
            policy,
            occupied,
            directory_assignments,
            directory_occupied,
            digest,
        )
        record["canonical_occurrence_id"] = str(record["occurrence_id"])
        record["canonical_path"] = relative_path
        if status == Status.ELIGIBLE.value:
            record["status"] = Status.PUBLISHED.value
        canonical_by_digest[digest] = (str(record["occurrence_id"]), relative_path)
        plan.append(record)
    return plan


def _assign_path(
    record: dict[str, Any],
    policy: Policy,
    occupied: dict[str, dict[str, str]],
    directory_assignments: dict[str, dict[str, str]],
    directory_occupied: dict[str, set[str]],
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
        directory_assignments,
        directory_occupied,
    )
    directory = "/".join(directory_parts)
    file_occupied = occupied.setdefault(collision_key(directory), {})
    requested_key = collision_key(requested_name)
    chosen = requested_name
    suffix = 0
    while requested_key in file_occupied and file_occupied[requested_key] != digest:
        suffix += 1
        chosen = suffixed_name(requested_name, suffix)
        requested_key = collision_key(chosen)
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
    file_occupied[requested_key] = digest
    return bounded_relative_path(
        [*directory_parts, chosen],
        protected_prefix=len(group_parts),
    )


def _portable_directories(
    parts: list[str],
    assignments: dict[str, dict[str, str]],
    occupied: dict[str, set[str]],
) -> list[str]:
    resolved: list[str] = []
    for part in parts:
        parent_key = collision_key("/".join(resolved))
        safe = safe_component(part)[0]
        identity = part.encode("utf-8", "surrogatepass").hex()
        parent_assignments = assignments.setdefault(parent_key, {})
        assignment_key = f"{collision_key(safe)}\0{identity}"
        assigned = parent_assignments.get(assignment_key)
        if assigned is None:
            parent_occupied = occupied.setdefault(parent_key, set())
            assigned = safe
            suffix = 0
            while collision_key(assigned) in parent_occupied:
                suffix += 1
                assigned = suffixed_name(safe, suffix)
            parent_occupied.add(collision_key(assigned))
            parent_assignments[assignment_key] = assigned
        resolved.append(assigned)
    return resolved
