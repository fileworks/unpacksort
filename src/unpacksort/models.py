"""Immutable domain models used across the processing pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any


class Group(str, Enum):
    """The fixed public output groups."""

    PDF = "pdf"
    IMAGES = "images"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENTS = "documents"
    SPREADSHEETS = "spreadsheets"
    PRESENTATIONS = "presentations"
    EBOOKS = "ebooks"
    FONTS = "fonts"
    DATA = "data"
    PACKAGES = "packages"
    EMAIL = "email"
    ARCHIVES_UNPROCESSED = "archives/unprocessed"
    OTHER = "other"


class DetectionMethod(str, Enum):
    """Portable detection evidence."""

    STRUCTURE = "structure"
    SIGNATURE = "signature"
    PACKAGE_PROFILE = "package_profile"
    PARSER = "parser"
    EXTENSION = "extension"
    UNKNOWN = "unknown"


class Status(str, Enum):
    """Terminal occurrence status."""

    ELIGIBLE = "eligible"
    PUBLISHED = "published"
    DUPLICATE = "duplicate"
    SKIPPED = "skipped"
    UNPROCESSED = "unprocessed"


class ExitOutcome(IntEnum):
    """Stable process outcomes."""

    SUCCESS = 0
    PARTIAL = 1
    USAGE = 2
    CONFLICT = 3
    FATAL = 4
    INTERRUPTED = 130


class Reason(str, Enum):
    """Stable reason codes emitted in manifests and reports."""

    POLICY_NON_PDF = "policy_non_pdf"
    UNSUPPORTED_RAR = "unsupported_rar"
    UNSUPPORTED_CONTAINER = "unsupported_container"
    ENCRYPTED_ARCHIVE = "encrypted_archive"
    CORRUPT_ARCHIVE = "corrupt_archive"
    ENCRYPTED_PDF = "encrypted_pdf"
    CORRUPT_PDF = "corrupt_pdf"
    UNSAFE_PATH = "unsafe_path"
    UNSAFE_ENTRY = "unsafe_entry"
    UNREADABLE = "unreadable"
    TRANSFER_DECODE_FAILED = "transfer_decode_failed"
    UNATTRIBUTABLE_MAIL_BYTES = "unattributable_mail_bytes"
    DEPTH_LIMIT = "depth_limit"
    MEMBER_COUNT_LIMIT = "member_count_limit"
    MEMBER_SIZE_LIMIT = "member_size_limit"
    CONTAINER_SIZE_LIMIT = "container_size_limit"
    GLOBAL_COUNT_LIMIT = "global_count_limit"
    GLOBAL_SIZE_LIMIT = "global_size_limit"
    EXPANSION_RATIO_LIMIT = "expansion_ratio_limit"
    CYCLE = "cycle"


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """A deterministic identity for the complete input."""

    kind: str
    root_name: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class AncestryNode:
    """One logical mail or archive ancestor."""

    kind: str
    identifier: str
    original_name: str | None = None


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """A deterministic type decision."""

    media_type: str
    group: Group
    extension: str
    method: DetectionMethod
    container: str | None = None
    reason: Reason | None = None
    registry_version: int = 1


@dataclass(frozen=True, slots=True)
class Blob:
    """A complete content-addressed staged payload."""

    digest: str
    size: int
    path: Path


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A stable diagnostic attached to an occurrence or container."""

    code: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One logical payload occurrence, including duplicates and failures."""

    occurrence_id: str
    sort_key: tuple[str, ...]
    source_path: str
    source_root: str
    ancestry: tuple[AncestryNode, ...]
    original_name: str | None
    generated_name: str
    detection: DetectionResult
    status: Status
    digest: str | None = None
    size: int | None = None
    reason: Reason | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    canonical_occurrence_id: str | None = None
    canonical_path: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable deterministic record."""

        record = asdict(self)
        record["sort_key"] = list(self.sort_key)
        record["ancestry"] = [asdict(node) for node in self.ancestry]
        record["diagnostics"] = [asdict(item) for item in self.diagnostics]
        record["detection"]["group"] = self.detection.group.value
        record["detection"]["method"] = self.detection.method.value
        if self.detection.reason is not None:
            record["detection"]["reason"] = self.detection.reason.value
        record["status"] = self.status.value
        if self.reason is not None:
            record["reason"] = self.reason.value
        return record


@dataclass(frozen=True, slots=True)
class ContainerRecord:
    """A logical container and its terminal expansion result."""

    container_id: str
    sort_key: tuple[str, ...]
    kind: str
    digest: str
    source_path: str
    ancestry: tuple[AncestryNode, ...]
    status: str
    reason: Reason | None = None
    member_count: int = 0
    expanded_bytes: int = 0

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable deterministic record."""

        record = asdict(self)
        record["sort_key"] = list(self.sort_key)
        record["ancestry"] = [asdict(node) for node in self.ancestry]
        if self.reason is not None:
            record["reason"] = self.reason.value
        return record
