from __future__ import annotations

import mailbox
from email.message import EmailMessage
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from unpacksort.mail import (
    is_confirmed_mbox,
    is_confirmed_message,
    iter_mbox,
    iter_message,
)
from unpacksort.models import AncestryNode, Reason


def test_mbox_preserves_message_and_part_order(mbox_path: Path) -> None:
    leaves = list(iter_mbox(mbox_path))
    assert [leaf.logical_name for leaf in leaves] == ["a.bin"]
    assert leaves[0].payload == b"payload"
    assert leaves[0].ancestry[0].identifier == "message-000001"
    assert leaves[0].metadata["mime_part_path"] == "part-0002"


def test_display_bodies_are_excluded_but_inline_resources_are_emitted() -> None:
    message = EmailMessage()
    message["From"] = "a@example.test"
    message["To"] = "b@example.test"
    message.set_content("plain")
    message.add_alternative("<html>html</html>", subtype="html")
    message.get_payload()[1].add_related(
        b"\x89PNG\r\n\x1a\n",
        maintype="image",
        subtype="png",
        cid="<image>",
    )
    leaves = list(
        iter_message(
            message.as_bytes(),
            (AncestryNode("message", "message-000001"),),
        ),
    )
    assert len(leaves) == 1
    assert leaves[0].metadata["content_id"] == "<image>"
    assert leaves[0].logical_name.startswith("part-")


def test_attached_text_is_not_treated_as_display_body() -> None:
    message = EmailMessage()
    message["From"] = "a@example.test"
    message["To"] = "b@example.test"
    message.set_content("body")
    message.add_attachment("notes", subtype="plain", filename="notes.txt")
    leaves = list(iter_message(message.as_bytes(), (AncestryNode("message", "m"),)))
    assert [leaf.logical_name for leaf in leaves] == ["notes.txt"]


def test_invalid_base64_is_durable_unprocessed_reason() -> None:
    payload = (
        b"From: a@example.test\r\nTo: b@example.test\r\nMIME-Version: 1.0\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n%%%% invalid %%%%\r\n"
    )
    leaves = list(iter_message(payload, (AncestryNode("message", "m"),)))
    assert len(leaves) == 1
    assert leaves[0].payload is None
    assert leaves[0].reason is Reason.TRANSFER_DECODE_FAILED


def test_attached_message_is_emitted_and_recursed() -> None:
    child = EmailMessage()
    child["From"] = "child@example.test"
    child["To"] = "recipient@example.test"
    child.set_content("child body")
    child.add_attachment(
        b"nested", maintype="application", subtype="octet-stream", filename="n.bin"
    )
    parent = EmailMessage()
    parent["From"] = "parent@example.test"
    parent["To"] = "recipient@example.test"
    parent.set_content("parent body")
    parent.add_attachment(child)
    leaves = list(iter_message(parent.as_bytes(), (AncestryNode("message", "root"),)))
    assert any(leaf.attached_message for leaf in leaves)
    assert any(leaf.logical_name == "n.bin" and leaf.payload == b"nested" for leaf in leaves)


def test_repeated_attached_messages_keep_independent_occurrences() -> None:
    child = EmailMessage()
    child["From"] = "child@example.test"
    child["To"] = "recipient@example.test"
    child.set_content("child body")
    child.add_attachment(
        b"same",
        maintype="application",
        subtype="octet-stream",
        filename="same.bin",
    )
    parent = EmailMessage()
    parent["From"] = "parent@example.test"
    parent["To"] = "recipient@example.test"
    parent.set_content("parent body")
    parent.add_attachment(child)
    parent.add_attachment(child)
    leaves = list(iter_message(parent.as_bytes(), (AncestryNode("message", "root"),)))
    atomic = [leaf for leaf in leaves if leaf.attached_message]
    nested = [leaf for leaf in leaves if leaf.logical_name == "same.bin"]
    assert len(atomic) == 2
    assert len(nested) == 2
    assert atomic[0].payload == atomic[1].payload
    assert nested[0].ancestry != nested[1].ancestry


def test_mail_recognition_requires_structural_evidence(tmp_path: Path) -> None:
    plain = tmp_path / "fake.eml"
    plain.write_text("ordinary text", encoding="utf-8")
    assert not is_confirmed_message(plain)
    assert not is_confirmed_mbox(plain)
    message = tmp_path / "message.dat"
    message.write_text(
        "From: a@example.test\nTo: b@example.test\nSubject: hello\n\nbody",
        encoding="utf-8",
    )
    assert is_confirmed_message(message)


def test_multiple_mbox_messages_have_stable_one_based_ids(tmp_path: Path) -> None:
    path = tmp_path / "many.mbox"
    box = mailbox.mbox(path, create=True)
    for index in range(2):
        message = EmailMessage()
        message["From"] = "a@example.test"
        message["To"] = "b@example.test"
        message.set_content("body")
        message.add_attachment(bytes([index]), maintype="application", subtype="octet-stream")
        box.add(message)
    box.flush()
    box.close()
    leaves = list(iter_mbox(path))
    assert [leaf.ancestry[0].identifier for leaf in leaves] == [
        "message-000001",
        "message-000002",
    ]


def test_unattributable_bytes_in_confirmed_mbox_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "malformed.mbox"
    path.write_bytes(
        b"From sender@example.test Sat Jan 01 00:00:00 2022\n"
        b"From: sender@example.test\nTo: recipient@example.test\n"
        b"Subject: valid\n\nordinary body\n"
        b"From broken Sat Jan 01 00:00:01 2022\n\nunattributable bytes\n",
    )
    assert is_confirmed_mbox(path)
    leaves = list(iter_mbox(path))
    assert len(leaves) == 1
    assert leaves[0].reason is Reason.UNATTRIBUTABLE_MAIL_BYTES
    assert leaves[0].ancestry[0].identifier == "message-000002"


@given(
    st.binary(max_size=64),
    st.text(alphabet=st.characters(categories=("Ll", "Lu")), min_size=1, max_size=20),
)
def test_mime_attachment_payload_and_name_round_trip(payload: bytes, stem: str) -> None:
    message = EmailMessage()
    message["From"] = "a@example.test"
    message["To"] = "b@example.test"
    message.set_content("body")
    message.add_attachment(
        payload,
        maintype="application",
        subtype="octet-stream",
        filename=f"{stem}.bin",
    )
    leaves = list(iter_message(message.as_bytes(), (AncestryNode("message", "m"),)))
    assert len(leaves) == 1
    assert leaves[0].payload == payload
    assert leaves[0].logical_name == f"{stem}.bin"
