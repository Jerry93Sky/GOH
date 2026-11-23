#!/usr/bin/env python3
"""Assemble the portable V0.2 ZIP without touching a game installation."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


DOCS = (
    "INSTALL_RU.md", "FIRST_RUN_RU.md", "TEST_A_RU.md", "TEST_B_RU.md",
    "TROUBLESHOOTING_RU.md", "RECOVERY_METADATA_TEMPLATE_RU.json",
    "P1_C_RESULTS.md", "OBSERVATION_RU.md", "RECOVERY_WIZARD_RU.md",
    "DIAGNOSTIC_REPORT_TEMPLATE_RU.json",
)
REQUIRED = {
    "GOHRecoveryManager.exe", "config/default.json", "docs/INSTALL_RU.md",
    "docs/FIRST_RUN_RU.md", "docs/TEST_A_RU.md", "docs/TEST_B_RU.md",
    "docs/TROUBLESHOOTING_RU.md", "docs/RECOVERY_METADATA_TEMPLATE_RU.json",
    "docs/P1_C_RESULTS.md", "docs/OBSERVATION_RU.md", "docs/RECOVERY_WIZARD_RU.md",
    "docs/DIAGNOSTIC_REPORT_TEMPLATE_RU.json", "checksums.sha256", "VERSION.txt",
}


def package(repository: Path, dist: Path) -> Path:
    exe = dist / "GOHRecoveryManager.exe"
    if not exe.is_file() or exe.stat().st_size < 100_000:
        raise RuntimeError("Windows manager EXE is missing or unexpectedly small")
    output = dist / "GOH-Coop-Recovery-V0.2-FieldTest-win64.zip"
    with tempfile.TemporaryDirectory(prefix="goh-dist-", dir=dist) as temporary:
        temp_root = Path(temporary)
        stage = temp_root / "GOH-Coop-Recovery"
        (stage / "config").mkdir(parents=True)
        (stage / "docs").mkdir()
        shutil.copy2(exe, stage / exe.name)
        shutil.copy2(repository / "config" / "default.json", stage / "config" / "default.json")
        for name in DOCS:
            shutil.copy2(repository / "docs" / name, stage / "docs" / name)
        (stage / "VERSION.txt").write_text(
            "GOH Coop Recovery V0.2 Field-Test\nStatus: EXPERIMENTAL; TEST-A=NOT_RUN; TEST-B=NOT_RUN\nArchitecture: Windows x64\nPython runtime: bundled by PyInstaller one-file executable\nThird-party runtime DLLs are not separately redistributed\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256((stage / "GOHRecoveryManager.exe").read_bytes()).hexdigest()
        (stage / "checksums.sha256").write_text(f"{digest}  GOHRecoveryManager.exe\n", encoding="ascii")
        (dist / "GOHRecoveryManager.exe.sha256").write_text(f"{digest}  GOHRecoveryManager.exe\n", encoding="ascii")
        temporary_zip = temp_root / output.name
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
        with zipfile.ZipFile(temporary_zip) as archive:
            names = set(archive.namelist())
            missing = REQUIRED - names
            if missing:
                raise RuntimeError("Distribution is missing: " + ", ".join(sorted(missing)))
        os.replace(temporary_zip, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    output = package(args.repository.resolve(), args.dist.resolve())
    print(f"Created: {output}\nBytes: {output.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
