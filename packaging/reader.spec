# PyInstaller spec for TaipowerBillReader.exe. Build with packaging/build.ps1.
# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=collect_dynamic_libs("onnxruntime"),
    datas=collect_data_files("pdfminer"),
    excludes=["torch", "tensorflow", "matplotlib", "IPython", "jupyter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TaipowerBillReader", console=True, upx=False)

coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="TaipowerBillReader")
