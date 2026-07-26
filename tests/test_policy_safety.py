from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from unpacksort.journal import Journal
from unpacksort.models import Reason
from unpacksort.policy import Accounting, LimitExceededError, Policy
from unpacksort.safety import (
    MAX_COMPONENT,
    MAX_RELATIVE_PATH,
    bounded_relative_path,
    collision_key,
    paths_alias,
    portable_text_key,
    safe_component,
    suffixed_name,
    unsafe_logical_path,
)
from unpacksort.storage import BlobStore


def test_policy_defaults_and_fingerprint_are_stable() -> None:
    policy = Policy()
    policy.validate()
    assert policy.fingerprint() == Policy().fingerprint()
    assert len(policy.fingerprint()) == 64


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"layout": "random"}, "layout"),
        ({"max_depth": 0}, "max_depth"),
        ({"max_members_per_container": 3, "max_members_run": 2}, "max_members"),
        ({"max_member_bytes": 3, "max_container_bytes": 2}, "max_member_bytes"),
        ({"max_container_bytes": 3, "max_run_bytes": 2}, "max_container_bytes"),
    ],
)
def test_policy_rejects_invalid_combinations(changes: dict[str, object], message: str) -> None:
    values = {
        "layout": "hierarchy",
        "max_depth": 10,
        "max_members_per_container": 10,
        "max_members_run": 100,
        "max_member_bytes": 10,
        "max_container_bytes": 100,
        "max_run_bytes": 1_000,
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        Policy(**values).validate()  # type: ignore[arg-type]


def test_accounting_charges_duplicate_occurrences() -> None:
    accounting = Accounting(Policy(max_members_run=2, max_run_bytes=5))
    accounting.charge(2)
    accounting.charge(2)
    with pytest.raises(LimitExceededError) as caught:
        accounting.charge(1)
    assert caught.value.reason is Reason.GLOBAL_COUNT_LIMIT


def test_accounting_enforces_global_bytes() -> None:
    accounting = Accounting(Policy(max_run_bytes=2, max_container_bytes=2, max_member_bytes=2))
    with pytest.raises(LimitExceededError) as caught:
        accounting.charge(3)
    assert caught.value.reason is Reason.GLOBAL_SIZE_LIMIT


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        r"C:\drive",
        r"\\server\share",
        "folder/../escape",
        "file:stream",
        "nul\x00byte",
    ],
)
def test_unsafe_logical_paths_are_rejected(name: str) -> None:
    assert unsafe_logical_path(name)


@pytest.mark.parametrize("name", ["folder/file.txt", "nested/a/b", "plain.txt"])
def test_safe_logical_paths_are_accepted(name: str) -> None:
    assert not unsafe_logical_path(name)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("CON.txt", "_CON.txt"),
        ("trailing. ", "trailing"),
        ("a<b>.txt", "a_b_.txt"),
        ("", "unnamed"),
    ],
)
def test_safe_component_is_windows_portable(source: str, expected: str) -> None:
    assert safe_component(source)[0] == expected


def test_safe_component_and_path_are_bounded() -> None:
    component = safe_component("a" * 400 + ".txt")[0]
    assert len(component.encode()) <= MAX_COMPONENT
    relative = bounded_relative_path(["images", *["long" * 20 for _ in range(10)], component])
    assert len(relative.encode()) <= MAX_RELATIVE_PATH


def test_long_multibyte_suffix_is_bounded() -> None:
    component, reasons = safe_component(f"file.{('😀' * 100)}")
    assert len(component.encode("utf-8")) <= MAX_COMPONENT
    assert "component_truncated" in reasons


def test_unicode_normalization_reason_is_retained() -> None:
    component, reasons = safe_component("cafe\u0301.txt")
    assert component == "café.txt"
    assert "unicode_nfc" in reasons


def test_path_compaction_preserves_multi_component_group() -> None:
    relative = bounded_relative_path(
        ["archives", "unprocessed", "nested" * 50, "😀" * 100],
        protected_prefix=2,
    )
    assert len(relative.encode("utf-8")) <= MAX_RELATIVE_PATH
    assert relative.startswith("archives/unprocessed/ancestry-")


def test_suffix_is_inserted_before_final_extension() -> None:
    assert suffixed_name("invoice.pdf", 2) == "invoice_2.pdf"
    assert suffixed_name("README", 1) == "README_1"


def test_paths_alias_handles_missing_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert paths_alias(missing, tmp_path / "." / "missing")
    assert not paths_alias(missing, tmp_path / "other")


def test_observed_stream_limit_discards_incomplete_blob(tmp_path: Path) -> None:
    with Journal(tmp_path / "destination") as journal:
        store = BlobStore(journal)
        with pytest.raises(LimitExceededError) as caught:
            store.ingest_bytes(b"metadata understated this payload", max_bytes=4)
        assert caught.value.reason is Reason.MEMBER_SIZE_LIMIT
        assert not list(store.temp.glob("blob-*.tmp"))


@given(st.text(max_size=80))
def test_collision_key_normalizes_unicode_and_case(value: str) -> None:
    normalized = unicodedata.normalize("NFC", value)
    assert collision_key(value) == normalized.casefold()


@given(st.text(max_size=80))
def test_portable_text_key_uses_normalized_bytes(value: str) -> None:
    folded, encoded = portable_text_key(value)
    normalized = unicodedata.normalize("NFC", value)
    assert folded == normalized.casefold()
    assert encoded == normalized.encode("utf-8", "surrogatepass")
