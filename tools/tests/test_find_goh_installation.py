from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is required for the discovery integration test")
class FindInstallationTests(unittest.TestCase):
    def test_discovers_manifest_in_additional_steam_library(self):
        repo = Path(__file__).resolve().parents[2]
        script = repo / "tools" / "find_goh_installation.ps1"
        with tempfile.TemporaryDirectory(prefix="goh-steam-library-test-") as temp:
            root = Path(temp)
            steam = root / "Steam"
            extra = root / "Extra Library"
            extra_escaped = str(extra).replace("\\", "\\\\")
            (steam / "steamapps").mkdir(parents=True)
            (steam / "steamapps" / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n{\n  "1"\n  {\n    "path" "' + extra_escaped + '"\n  }\n}\n',
                encoding="utf-8",
            )
            game = extra / "steamapps" / "common" / "GoH Test Install"
            x64 = game / "binaries" / "x64"
            x64.mkdir(parents=True)
            (x64 / "call_to_arms.exe").write_bytes(b"synthetic-only")
            (x64 / "call_to_arms_server.exe").write_bytes(b"synthetic-only")
            manifest = extra / "steamapps" / "appmanifest_123.acf"
            manifest.write_text(
                '"AppState" {\n "name" "Call to Arms - Gates of Hell: Ostfront"\n "installdir" "GoH Test Install"\n}\n',
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(script), "-SteamRoot", str(steam)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            report = json.loads(proc.stdout)
            self.assertEqual(len(report["Installations"]), 1)
            self.assertEqual(Path(report["Installations"][0]["GamePath"]), game)
            self.assertTrue(report["Installations"][0]["LocalServerExe"])
            self.assertTrue(report["ReadOnly"])


if __name__ == "__main__":
    unittest.main()
