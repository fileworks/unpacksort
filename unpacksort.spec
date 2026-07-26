# -*- mode: python ; coding: utf-8 -*-
"""Pinned Windows x64 PyInstaller entry point."""

from PyInstaller.utils.hooks import collect_all, copy_metadata

pikepdf_datas, pikepdf_binaries, pikepdf_hidden = collect_all("pikepdf")
py7zr_datas, py7zr_binaries, py7zr_hidden = collect_all("py7zr")
unpacksort_metadata = copy_metadata("unpacksort")

analysis = Analysis(
    ["src/unpacksort/__main__.py"],
    pathex=["src"],
    binaries=[*pikepdf_binaries, *py7zr_binaries],
    datas=[*pikepdf_datas, *py7zr_datas, *unpacksort_metadata],
    hiddenimports=[*pikepdf_hidden, *py7zr_hidden],
    noarchive=False,
)
python_archive = PYZ(analysis.pure)
executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="unpacksort",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
