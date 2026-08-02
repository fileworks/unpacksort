"""Deterministic mailbox and RFC 5322 traversal."""

from __future__ import annotations

import hashlib
import mailbox
from collections.abc import Iterator
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from mailbox import _GetFileReturn

from unpacksort.models import AncestryNode, Diagnostic, Reason

MAIL_POLICY = policy.default.clone(
    utf8=True,
    refold_source="none",
    raise_on_defect=False,
)
IDENTITY_HEADERS = ("message-id", "date", "from", "to", "subject")
MIN_IDENTITY_HEADERS = 2
RECOGNITION_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class MailLeaf:
    """One emitted or failed non-body MIME leaf."""

    logical_name: str
    ancestry: tuple[AncestryNode, ...]
    metadata: dict[str, str]
    diagnostics: tuple[Diagnostic, ...]
    payload: bytes | None
    reason: Reason | None = None
    attached_message: bool = False


def is_confirmed_mbox(path: Path) -> bool:
    """Recognize an mbox through delimiter and parseable-message evidence."""

    try:
        with path.open("rb") as stream:
            first = stream.readline(4096)
        if not first.startswith(b"From "):
            return False
        box = mailbox.mbox(path, factory=_parse_mailbox_message, create=False)
        try:
            return any(_has_message_evidence(message) for message in box)
        finally:
            box.close()
    except (OSError, mailbox.Error):
        return False


def is_confirmed_message(path: Path) -> bool:
    """Conservatively recognize a standalone RFC 5322 message."""

    try:
        with path.open("rb") as stream:
            payload = stream.read(RECOGNITION_BYTES)
    except OSError:
        return False
    message = BytesParser(policy=MAIL_POLICY).parsebytes(payload)
    headers = {name.casefold() for name in message}
    identity_count = len(headers.intersection(IDENTITY_HEADERS))
    structural = bool(headers.intersection({"mime-version", "content-type"}))
    return identity_count >= MIN_IDENTITY_HEADERS or (identity_count >= 1 and structural)


def iter_mbox(path: Path) -> Iterator[MailLeaf]:
    """Yield leaves in physical message and parser MIME order."""

    box = mailbox.mbox(path, factory=_parse_mailbox_message, create=False)
    try:
        found = False
        for message_index, message in enumerate(box, start=1):
            found = True
            message_id = f"message-{message_index:06d}"
            root = (AncestryNode("message", message_id),)
            if not _has_message_evidence(message):
                yield MailLeaf(
                    logical_name="unattributable-mail.bin",
                    ancestry=root,
                    metadata={},
                    diagnostics=(Diagnostic(Reason.UNATTRIBUTABLE_MAIL_BYTES.value),),
                    payload=None,
                    reason=Reason.UNATTRIBUTABLE_MAIL_BYTES,
                )
                continue
            digest = hashlib.sha256(message.as_bytes(policy=MAIL_POLICY)).hexdigest()
            yield from _walk_message(message, root, (), frozenset({digest}), ())
        if not found and path.stat().st_size:
            yield MailLeaf(
                logical_name="unattributable-mail.bin",
                ancestry=(),
                metadata={},
                diagnostics=(Diagnostic(Reason.UNATTRIBUTABLE_MAIL_BYTES.value),),
                payload=None,
                reason=Reason.UNATTRIBUTABLE_MAIL_BYTES,
            )
    finally:
        box.close()


def iter_message(payload: bytes, ancestry: tuple[AncestryNode, ...]) -> Iterator[MailLeaf]:
    """Traverse a standalone or attached RFC 5322 message."""

    message = BytesParser(policy=MAIL_POLICY).parsebytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    yield from _walk_message(message, ancestry, (), frozenset({digest}), ())


def iter_message_path(
    path: Path,
    ancestry: tuple[AncestryNode, ...],
    digest: str,
) -> Iterator[MailLeaf]:
    """Traverse a bounded staged RFC 5322 message without a second raw copy."""

    with path.open("rb") as stream:
        message = BytesParser(policy=MAIL_POLICY).parse(stream)
    yield from _walk_message(message, ancestry, (), frozenset({digest}), ())


def _parse_mailbox_message(stream: _GetFileReturn) -> mailbox.mboxMessage:
    # mailbox and email expose different private stream protocols in typeshed even
    # though mailbox's proxy implements the binary interface BytesParser consumes.
    parsed = BytesParser(policy=MAIL_POLICY).parse(cast(Any, stream))
    return mailbox.mboxMessage(parsed)


def _has_message_evidence(message: Message) -> bool:
    headers = {name.casefold() for name in message}
    identity_count = len(headers.intersection(IDENTITY_HEADERS))
    structural = bool(headers.intersection({"mime-version", "content-type"}))
    return identity_count >= MIN_IDENTITY_HEADERS or (identity_count >= 1 and structural)


def _walk_message(
    message: Message,
    ancestry: tuple[AncestryNode, ...],
    part_path: tuple[int, ...],
    ancestor_digests: frozenset[str],
    ancestor_diagnostics: tuple[Diagnostic, ...],
) -> Iterator[MailLeaf]:
    node_diagnostics = (*ancestor_diagnostics, *_defects(message))
    headers = {
        name: str(message.get(name, ""))
        for name in IDENTITY_HEADERS
        if message.get(name) is not None
    }
    children = message.get_payload()
    if message.get_content_type() == "message/rfc822":
        attached = children if isinstance(children, list) else []
        for child_index, child in enumerate(attached, start=1):
            if not isinstance(child, Message):
                continue
            child_path = (*part_path, child_index)
            part_identifier = _part_identifier(child_path)
            raw = child.as_bytes(policy=MAIL_POLICY)
            digest = hashlib.sha256(raw).hexdigest()
            node = AncestryNode("mime_part", part_identifier, "attached-message.eml")
            metadata = _part_metadata(message, headers, part_identifier)
            yield MailLeaf(
                logical_name="attached-message.eml",
                ancestry=(*ancestry, node),
                metadata=metadata,
                diagnostics=node_diagnostics,
                payload=raw,
                attached_message=True,
            )
            if digest in ancestor_digests:
                yield MailLeaf(
                    logical_name="attached-message.eml",
                    ancestry=(*ancestry, node, AncestryNode("message", "cycle")),
                    metadata=metadata,
                    diagnostics=(*node_diagnostics, Diagnostic(Reason.CYCLE.value)),
                    payload=None,
                    reason=Reason.CYCLE,
                )
                continue
            nested = AncestryNode("message", f"{part_identifier}-message")
            yield from _walk_message(
                child,
                (*ancestry, node, nested),
                (),
                ancestor_digests | {digest},
                node_diagnostics,
            )
        return
    if message.is_multipart():
        payloads = children if isinstance(children, list) else []
        for child_index, child in enumerate(payloads, start=1):
            if isinstance(child, Message):
                yield from _walk_message(
                    child,
                    ancestry,
                    (*part_path, child_index),
                    ancestor_digests,
                    node_diagnostics,
                )
        return
    if _is_display_body(message):
        return
    identifier = _part_identifier(part_path or (1,))
    filename = message.get_filename()
    logical_name = filename or f"part-{(part_path or (1,))[-1]:04d}.bin"
    metadata = _part_metadata(message, headers, identifier)
    decoded = message.get_payload(decode=True)
    diagnostics = (*ancestor_diagnostics, *_defects(message))
    transfer_failed = any("base64" in item.code.casefold() for item in diagnostics)
    if transfer_failed or not isinstance(decoded, bytes):
        yield MailLeaf(
            logical_name=logical_name,
            ancestry=(*ancestry, AncestryNode("mime_part", identifier, filename)),
            metadata=metadata,
            diagnostics=(*diagnostics, Diagnostic(Reason.TRANSFER_DECODE_FAILED.value)),
            payload=None,
            reason=Reason.TRANSFER_DECODE_FAILED,
        )
        return
    yield MailLeaf(
        logical_name=logical_name,
        ancestry=(*ancestry, AncestryNode("mime_part", identifier, filename)),
        metadata=metadata,
        diagnostics=diagnostics,
        payload=decoded,
    )


def _is_display_body(message: Message) -> bool:
    content_type = message.get_content_type().casefold()
    disposition = message.get_content_disposition()
    name_parameter = message.get_param("name")
    content_id = message.get("Content-ID")
    return (
        content_type in {"text/plain", "text/html"}
        and disposition != "attachment"
        and message.get_filename() is None
        and name_parameter is None
        and content_id is None
    )


def _part_identifier(path: tuple[int, ...]) -> str:
    return "part-" + ".".join(f"{index:04d}" for index in path)


def _defects(message: Message) -> tuple[Diagnostic, ...]:
    return tuple(Diagnostic(type(defect).__name__, str(defect)) for defect in message.defects)


def _part_metadata(
    message: Message,
    headers: dict[str, str],
    identifier: str,
) -> dict[str, str]:
    result = dict(headers)
    result.update(
        {
            "mime_part_path": identifier,
            "content_type": message.get_content_type(),
            "content_disposition": message.get_content_disposition() or "",
            "content_id": str(message.get("Content-ID", "")),
            "filename": message.get_filename() or "",
            "name_parameter": str(message.get_param("name") or ""),
            "content_transfer_encoding": str(message.get("Content-Transfer-Encoding", "")),
        },
    )
    return result
