"""Versioned portable type detection without a host libmagic database."""

from __future__ import annotations

import json
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pikepdf

from unpacksort.models import DetectionMethod, DetectionResult, Group, Reason

REGISTRY_VERSION = 1
MARKER_LIMIT = 64 * 1024
MIN_FTYP_HEADER = 12


@dataclass(frozen=True, slots=True)
class PackageProfile:
    """A bounded ZIP application-package profile."""

    name: str
    group: Group
    extension: str
    matches: Callable[[set[str], Callable[[str], bytes]], bool]


def _has_prefix(names: set[str], prefix: str) -> bool:
    return any(name.startswith(prefix) for name in names)


PACKAGE_PROFILES = (
    PackageProfile(
        "ooxml-word",
        Group.DOCUMENTS,
        ".docx",
        lambda names, _read: "[Content_Types].xml" in names and _has_prefix(names, "word/"),
    ),
    PackageProfile(
        "ooxml-sheet",
        Group.SPREADSHEETS,
        ".xlsx",
        lambda names, _read: "[Content_Types].xml" in names and _has_prefix(names, "xl/"),
    ),
    PackageProfile(
        "ooxml-presentation",
        Group.PRESENTATIONS,
        ".pptx",
        lambda names, _read: "[Content_Types].xml" in names and _has_prefix(names, "ppt/"),
    ),
    PackageProfile(
        "odf-text",
        Group.DOCUMENTS,
        ".odt",
        lambda names, read: "mimetype" in names
        and read("mimetype").strip() == b"application/vnd.oasis.opendocument.text",
    ),
    PackageProfile(
        "odf-sheet",
        Group.SPREADSHEETS,
        ".ods",
        lambda names, read: "mimetype" in names
        and read("mimetype").strip() == b"application/vnd.oasis.opendocument.spreadsheet",
    ),
    PackageProfile(
        "odf-presentation",
        Group.PRESENTATIONS,
        ".odp",
        lambda names, read: "mimetype" in names
        and read("mimetype").strip() == b"application/vnd.oasis.opendocument.presentation",
    ),
    PackageProfile(
        "epub",
        Group.EBOOKS,
        ".epub",
        lambda names, read: "mimetype" in names
        and read("mimetype").strip() == b"application/epub+zip",
    ),
    PackageProfile(
        "java-war",
        Group.PACKAGES,
        ".war",
        lambda names, _read: "WEB-INF/web.xml" in names,
    ),
    PackageProfile(
        "java-ear",
        Group.PACKAGES,
        ".ear",
        lambda names, _read: "META-INF/application.xml" in names,
    ),
    PackageProfile(
        "jar",
        Group.PACKAGES,
        ".jar",
        lambda names, _read: "META-INF/MANIFEST.MF" in names,
    ),
    PackageProfile(
        "android-apk",
        Group.PACKAGES,
        ".apk",
        lambda names, _read: "AndroidManifest.xml" in names and "classes.dex" in names,
    ),
    PackageProfile(
        "android-bundle",
        Group.PACKAGES,
        ".aab",
        lambda names, _read: "BundleConfig.pb" in names
        and "base/manifest/AndroidManifest.xml" in names,
    ),
    PackageProfile(
        "python-wheel",
        Group.PACKAGES,
        ".whl",
        lambda names, _read: any(name.endswith(".dist-info/WHEEL") for name in names),
    ),
    PackageProfile(
        "nuget",
        Group.PACKAGES,
        ".nupkg",
        lambda names, _read: any(name.endswith(".nuspec") for name in names),
    ),
    PackageProfile(
        "vsix",
        Group.PACKAGES,
        ".vsix",
        lambda names, _read: "extension.vsixmanifest" in names,
    ),
)

EXTENSIONS: dict[str, tuple[str, Group, str]] = {
    ".png": ("image/png", Group.IMAGES, ".png"),
    ".jpg": ("image/jpeg", Group.IMAGES, ".jpg"),
    ".jpeg": ("image/jpeg", Group.IMAGES, ".jpg"),
    ".gif": ("image/gif", Group.IMAGES, ".gif"),
    ".tif": ("image/tiff", Group.IMAGES, ".tif"),
    ".tiff": ("image/tiff", Group.IMAGES, ".tif"),
    ".webp": ("image/webp", Group.IMAGES, ".webp"),
    ".mp4": ("video/mp4", Group.VIDEO, ".mp4"),
    ".mov": ("video/quicktime", Group.VIDEO, ".mov"),
    ".mkv": ("video/x-matroska", Group.VIDEO, ".mkv"),
    ".mp3": ("audio/mpeg", Group.AUDIO, ".mp3"),
    ".wav": ("audio/wav", Group.AUDIO, ".wav"),
    ".flac": ("audio/flac", Group.AUDIO, ".flac"),
    ".doc": ("application/msword", Group.DOCUMENTS, ".doc"),
    ".rtf": ("application/rtf", Group.DOCUMENTS, ".rtf"),
    ".txt": ("text/plain", Group.DOCUMENTS, ".txt"),
    ".csv": ("text/csv", Group.SPREADSHEETS, ".csv"),
    ".xls": ("application/vnd.ms-excel", Group.SPREADSHEETS, ".xls"),
    ".ppt": ("application/vnd.ms-powerpoint", Group.PRESENTATIONS, ".ppt"),
    ".mobi": ("application/x-mobipocket-ebook", Group.EBOOKS, ".mobi"),
    ".ttf": ("font/ttf", Group.FONTS, ".ttf"),
    ".otf": ("font/otf", Group.FONTS, ".otf"),
    ".json": ("application/json", Group.DATA, ".json"),
    ".xml": ("application/xml", Group.DATA, ".xml"),
    ".yaml": ("application/yaml", Group.DATA, ".yaml"),
    ".yml": ("application/yaml", Group.DATA, ".yaml"),
    ".sqlite": ("application/vnd.sqlite3", Group.DATA, ".sqlite"),
    ".db": ("application/vnd.sqlite3", Group.DATA, ".sqlite"),
    ".eml": ("message/rfc822", Group.EMAIL, ".eml"),
}


def detect(path: Path, logical_name: str) -> DetectionResult:
    """Detect one complete staged blob using portable evidence precedence."""

    with path.open("rb") as stream:
        head = stream.read(MARKER_LIMIT)
    suffix = PurePosixPath(logical_name).suffix.casefold()
    if head.startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")):
        return DetectionResult(
            "application/vnd.rar",
            Group.ARCHIVES_UNPROCESSED,
            ".rar",
            DetectionMethod.SIGNATURE,
            container="rar",
            reason=Reason.UNSUPPORTED_RAR,
        )
    if head.startswith(b"7z\xbc\xaf'\x1c"):
        return DetectionResult(
            "application/x-7z-compressed",
            Group.OTHER,
            ".7z",
            DetectionMethod.SIGNATURE,
            container="7z",
        )
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        package = _detect_zip_package(path)
        if package is not None:
            return package
        return DetectionResult(
            "application/zip",
            Group.OTHER,
            ".zip",
            DetectionMethod.STRUCTURE,
            container="zip",
        )
    if _is_tar(path, suffix):
        return DetectionResult(
            "application/x-tar",
            Group.OTHER,
            ".tar",
            DetectionMethod.STRUCTURE,
            container="tar",
        )
    if head.startswith(b"%PDF-") or suffix == ".pdf":
        return _detect_pdf(path)
    signatures: tuple[tuple[bytes, str, Group, str], ...] = (
        (b"\x89PNG\r\n\x1a\n", "image/png", Group.IMAGES, ".png"),
        (b"\xff\xd8\xff", "image/jpeg", Group.IMAGES, ".jpg"),
        (b"GIF8", "image/gif", Group.IMAGES, ".gif"),
        (b"II*\x00", "image/tiff", Group.IMAGES, ".tif"),
        (b"MM\x00*", "image/tiff", Group.IMAGES, ".tif"),
        (b"ID3", "audio/mpeg", Group.AUDIO, ".mp3"),
        (b"fLaC", "audio/flac", Group.AUDIO, ".flac"),
        (b"SQLite format 3\x00", "application/vnd.sqlite3", Group.DATA, ".sqlite"),
        (b"{", "application/json", Group.DATA, ".json"),
        (b"[", "application/json", Group.DATA, ".json"),
    )
    for signature, media_type, group, extension in signatures:
        if head.startswith(signature):
            if media_type == "application/json":
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
            return DetectionResult(
                media_type,
                group,
                extension,
                DetectionMethod.SIGNATURE,
            )
    if len(head) >= MIN_FTYP_HEADER and head[4:8] == b"ftyp":
        return DetectionResult("video/mp4", Group.VIDEO, ".mp4", DetectionMethod.SIGNATURE)
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return DetectionResult("audio/wav", Group.AUDIO, ".wav", DetectionMethod.SIGNATURE)
    fallback = EXTENSIONS.get(suffix)
    if fallback is not None:
        media_type, group, extension = fallback
        return DetectionResult(media_type, group, extension, DetectionMethod.EXTENSION)
    return DetectionResult(
        "application/octet-stream",
        Group.OTHER,
        suffix or ".bin",
        DetectionMethod.UNKNOWN,
    )


def _detect_pdf(path: Path) -> DetectionResult:
    try:
        with pikepdf.open(path):
            pass
    except pikepdf.PasswordError:
        return DetectionResult(
            "application/pdf",
            Group.ARCHIVES_UNPROCESSED,
            ".pdf",
            DetectionMethod.PARSER,
            reason=Reason.ENCRYPTED_PDF,
        )
    except pikepdf.PdfError:
        return DetectionResult(
            "application/pdf",
            Group.ARCHIVES_UNPROCESSED,
            ".pdf",
            DetectionMethod.PARSER,
            reason=Reason.CORRUPT_PDF,
        )
    return DetectionResult("application/pdf", Group.PDF, ".pdf", DetectionMethod.PARSER)


def _is_tar(path: Path, suffix: str) -> bool:
    if suffix in {".tar", ".tgz", ".tbz", ".tbz2", ".txz", ".tzst"}:
        return True
    if path.name.casefold().endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")):
        return True
    try:
        return tarfile.is_tarfile(path)
    except OSError:
        return False


def _detect_zip_package(path: Path) -> DetectionResult | None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = {info.filename.replace("\\", "/") for info in infos}

            def bounded_read(name: str) -> bytes:
                info = archive.getinfo(name)
                if info.file_size > MARKER_LIMIT:
                    return b""
                with archive.open(info) as marker:
                    return marker.read(MARKER_LIMIT + 1)[:MARKER_LIMIT]

            for profile in PACKAGE_PROFILES:
                if profile.matches(names, bounded_read):
                    return DetectionResult(
                        f"application/x-{profile.name}",
                        profile.group,
                        profile.extension,
                        DetectionMethod.PACKAGE_PROFILE,
                    )
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        return None
    return None
