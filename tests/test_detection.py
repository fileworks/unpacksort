from __future__ import annotations

import zipfile
from pathlib import Path

import pikepdf
import pytest

from unpacksort.detection import PACKAGE_PROFILES, detect
from unpacksort.models import DetectionMethod, Group, Reason


def test_valid_pdf_requires_parser_evidence(valid_pdf: Path) -> None:
    result = detect(valid_pdf, "misleading.bin")
    assert result.group is Group.PDF
    assert result.extension == ".pdf"
    assert result.method is DetectionMethod.PARSER


def test_corrupt_pdf_is_unprocessed(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\nnot a valid document")
    result = detect(path, path.name)
    assert result.group is Group.ARCHIVES_UNPROCESSED
    assert result.reason is Reason.CORRUPT_PDF


def test_encrypted_pdf_is_unprocessed(tmp_path: Path) -> None:
    path = tmp_path / "secret.pdf"
    document = pikepdf.Pdf.new()
    document.add_blank_page(page_size=(72, 72))
    document.save(path, encryption=pikepdf.Encryption(user="secret", owner="owner"))
    result = detect(path, path.name)
    assert result.reason is Reason.ENCRYPTED_PDF


@pytest.mark.parametrize(
    ("payload", "name", "group", "extension"),
    [
        (b"\x89PNG\r\n\x1a\nrest", "wrong.txt", Group.IMAGES, ".png"),
        (b"\xff\xd8\xffrest", "wrong.bin", Group.IMAGES, ".jpg"),
        (b"ID3rest", "wrong.bin", Group.AUDIO, ".mp3"),
        (b"fLaCrest", "wrong.bin", Group.AUDIO, ".flac"),
        (b"SQLite format 3\x00rest", "wrong.bin", Group.DATA, ".sqlite"),
        (b"\x00\x00\x00\x18ftypisom", "wrong.bin", Group.VIDEO, ".mp4"),
        (b"RIFF\x00\x00\x00\x00WAVE", "wrong.bin", Group.AUDIO, ".wav"),
    ],
)
def test_signatures_override_extensions(
    tmp_path: Path,
    payload: bytes,
    name: str,
    group: Group,
    extension: str,
) -> None:
    path = tmp_path / name
    path.write_bytes(payload)
    result = detect(path, name)
    assert result.group is group
    assert result.extension == extension
    assert result.method is DetectionMethod.SIGNATURE


def test_valid_json_and_extension_fallback(tmp_path: Path) -> None:
    json_path = tmp_path / "unknown.bin"
    json_path.write_text('{"value": 1}', encoding="utf-8")
    assert detect(json_path, json_path.name).group is Group.DATA
    text_path = tmp_path / "readme.txt"
    text_path.write_text("hello", encoding="utf-8")
    assert detect(text_path, text_path.name).method is DetectionMethod.EXTENSION


def test_unknown_content_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "mystery"
    path.write_bytes(b"\x01\x02\x03")
    result = detect(path, path.name)
    assert result.group is Group.OTHER
    assert result.extension == ".bin"


def test_rar_is_recognized_without_extraction(tmp_path: Path) -> None:
    path = tmp_path / "archive.bin"
    path.write_bytes(b"Rar!\x1a\x07\x01\x00payload")
    result = detect(path, path.name)
    assert result.container == "rar"
    assert result.reason is Reason.UNSUPPORTED_RAR


def test_generic_zip_is_a_container_despite_package_extension(tmp_path: Path) -> None:
    path = tmp_path / "fake.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plain.txt", "payload")
    result = detect(path, path.name)
    assert result.container == "zip"
    assert result.method is DetectionMethod.STRUCTURE


@pytest.mark.parametrize(
    ("marker_name", "marker", "extra_name", "extension"),
    [
        ("[Content_Types].xml", b"<Types/>", "word/document.xml", ".docx"),
        ("[Content_Types].xml", b"<Types/>", "xl/workbook.xml", ".xlsx"),
        ("[Content_Types].xml", b"<Types/>", "ppt/presentation.xml", ".pptx"),
        ("mimetype", b"application/vnd.oasis.opendocument.text", "content.xml", ".odt"),
        (
            "mimetype",
            b"application/vnd.oasis.opendocument.spreadsheet",
            "content.xml",
            ".ods",
        ),
        (
            "mimetype",
            b"application/vnd.oasis.opendocument.presentation",
            "content.xml",
            ".odp",
        ),
        ("mimetype", b"application/epub+zip", "META-INF/container.xml", ".epub"),
        ("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0", "A.class", ".jar"),
        ("WEB-INF/web.xml", b"<web-app/>", "index.jsp", ".war"),
        ("META-INF/application.xml", b"<application/>", "module.jar", ".ear"),
        ("AndroidManifest.xml", b"binary", "classes.dex", ".apk"),
        ("BundleConfig.pb", b"binary", "base/manifest/AndroidManifest.xml", ".aab"),
        ("demo-1.dist-info/WHEEL", b"Wheel-Version: 1.0", "demo.py", ".whl"),
        ("demo.nuspec", b"<package/>", "lib/demo.dll", ".nupkg"),
        ("extension.vsixmanifest", b"<PackageManifest/>", "extension/file", ".vsix"),
    ],
)
def test_zip_package_profiles_are_structural(
    tmp_path: Path,
    marker_name: str,
    marker: bytes,
    extra_name: str,
    extension: str,
) -> None:
    path = tmp_path / "misleading.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(marker_name, marker)
        archive.writestr(extra_name, b"x")
    result = detect(path, "missing-extension")
    assert result.container is None
    assert result.extension == extension
    assert result.method is DetectionMethod.PACKAGE_PROFILE


def test_package_registry_is_versioned_and_complete() -> None:
    assert len(PACKAGE_PROFILES) >= 10
    assert {profile.name for profile in PACKAGE_PROFILES} >= {
        "ooxml-word",
        "epub",
        "jar",
        "android-apk",
        "python-wheel",
    }
