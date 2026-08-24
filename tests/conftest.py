from __future__ import annotations

import mailbox
import os
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pikepdf
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Keep the opt-in scale command about its production-path budget.

    The scheduled scale workflow already clears the global coverage addopts.
    A direct focused invocation should have the same meaning: it still reports
    the exercised lines, but a single scale test cannot satisfy the full-suite
    90% aggregate threshold. Full-suite invocations retain that threshold.
    """
    if os.environ.get("UNPACKSORT_SCALE_TIER") is None:
        return
    targets = [
        argument for argument in config.invocation_params.args if not str(argument).startswith("-")
    ]
    if targets and all(
        Path(str(target).split("::", 1)[0]).name == "test_scale_budgets.py" for target in targets
    ):
        config.option.cov_fail_under = 0
        coverage_plugin: Any = config.pluginmanager.get_plugin("_cov")
        if coverage_plugin is not None:
            coverage_plugin.options.cov_fail_under = 0


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
