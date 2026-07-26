from __future__ import annotations

import mailbox
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path

import pikepdf
import pytest


@pytest.fixture
def valid_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "valid.pdf"
    document = pikepdf.Pdf.new()
    document.add_blank_page(page_size=(72, 72))
    document.save(path)
    return path


@pytest.fixture
def mbox_path(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "mail.mbox"
    box = mailbox.mbox(path, create=True)
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["To"] = "recipient@example.test"
    message["Subject"] = "fixture"
    message["Message-ID"] = "<fixture@example.test>"
    message.set_content("display body")
    message.add_attachment(
        b"payload", maintype="application", subtype="octet-stream", filename="a.bin"
    )
    box.add(message)
    box.flush()
    box.close()
    try:
        yield path
    finally:
        if path.exists():
            mailbox.mbox(path).close()
