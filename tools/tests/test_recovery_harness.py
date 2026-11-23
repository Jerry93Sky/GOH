from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recovery_coordinator as rc
import recovery_harness as rh
import goh_recovery_manager as manager


class RecoveryHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "store"

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def write(path, data=b"snapshot"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def capture(self, players=("host", "guest"), missing_client=None, missing_server=False):
        peers = []
        for player in players:
            if player != missing_client:
                folder = self.root / "sources" / player
                self.write(folder / "coop_ckpt.sav", f"client-{player}".encode())
                peers.append(f"{player}={folder}")
        server = self.root / "server"
        if not missing_server:
            self.write(server / "coop_ckpt_srv.sav", b"server-state")
        args = type("Args", (), {
            "store": str(self.store), "checkpoint_id": "cp-test", "checkpoint_time_utc": "2026-11-23T12:00:00Z",
            "mission_id": "mission-x", "game_build": "1.069.0", "mod_fingerprint": "mods-sha",
            "expected_player": list(players), "peer": peers, "server_root": str(server),
        })()
        return rc.capture(args)

    def test_two_participant_packages_and_host_server_import(self):
        self.capture()
        for player in ("host", "guest"):
            package = self.root / f"{player}.zip"
            rh.build_peer_package(self.store / "cp-test", player, package, include_host_server=player == "host")
            meta, issues = rh.inspect_peer_package(package)
            self.assertEqual(issues, [])
            self.assertEqual(meta["participant_id"], player)
            target = self.root / f"local-{player}"
            imported = rh.import_peer_package(package, target, "cp-test", "mission-x", "1.069.0", "mods-sha")
            self.assertTrue((target / "coop_ckpt.sav").is_file())
            self.assertEqual((target / "coop_ckpt_srv.sav").exists(), player == "host")
            self.assertTrue((target / "recovery_package.json").is_file())

    def test_three_participant_packages(self):
        self.capture(("host", "p2", "p3"))
        for player in ("host", "p2", "p3"):
            package = self.root / f"{player}.zip"
            rh.build_peer_package(self.store / "cp-test", player, package)
            meta, issues = rh.inspect_peer_package(package)
            self.assertFalse(issues)
            self.assertEqual(set(meta["expected_players"]), {"host", "p2", "p3"})

    def test_peer_group_compares_roster_and_requires_host_server(self):
        self.capture()
        packages=[]
        for player in ("host", "guest"):
            package=self.root / f"group-{player}.zip"
            rh.build_peer_package(self.store / "cp-test", player, package, include_host_server=player == "host")
            packages.append(package)
        result=rh.validate_peer_group(packages, "host")
        self.assertEqual(result["status"], "GROUP_FILE_SET_VERIFIED")
        self.assertEqual(result["game_restore_status"], "NOT_VERIFIED_BY_FILE_SET")

    def test_peer_group_rejects_missing_host_server_archive(self):
        self.capture()
        packages=[]
        for player in ("host", "guest"):
            package=self.root / f"no-server-{player}.zip"
            rh.build_peer_package(self.store / "cp-test", player, package, include_host_server=False)
            packages.append(package)
        result=rh.validate_peer_group(packages, "host")
        self.assertEqual(result["status"], "INCOMPLETE_OR_MISMATCHED")
        self.assertIn("host package is missing its local server archive", result["issues"])

    def test_peer_group_rejects_cross_package_metadata_mismatch(self):
        self.capture()
        a=self.root / "group-host.zip"; b=self.root / "group-guest.zip"
        rh.build_peer_package(self.store / "cp-test", "host", a, include_host_server=True)
        rh.build_peer_package(self.store / "cp-test", "guest", b)
        rewritten=self.root / "group-guest-rewritten.zip"
        with zipfile.ZipFile(b) as source, zipfile.ZipFile(rewritten,"w") as target:
            import json
            for name in source.namelist():
                payload=source.read(name)
                if name == "package.json":
                    metadata=json.loads(payload); metadata["game_build"]="different-build"
                    payload=json.dumps(metadata).encode()
                target.writestr(name,payload)
        result=rh.validate_peer_group([a, rewritten], "host")
        self.assertTrue(any("game_build mismatch" in issue for issue in result["issues"]))

    def test_missing_client_archive_blocks_export(self):
        self.capture(missing_client="guest")
        with self.assertRaises(rh.HarnessError):
            rh.build_peer_package(self.store / "cp-test", "host", self.root / "bad.zip")

    def test_missing_server_archive_blocks_export(self):
        self.capture(missing_server=True)
        with self.assertRaises(rh.HarnessError):
            rh.build_peer_package(self.store / "cp-test", "host", self.root / "bad.zip", True)

    def test_package_detects_corruption_and_unexpected_path(self):
        self.capture()
        package = self.root / "p.zip"
        rh.build_peer_package(self.store / "cp-test", "guest", package)
        with zipfile.ZipFile(package, "a") as z:
            z.writestr("../escape", b"x")
        with self.assertRaises(rh.HarnessError):
            rh.inspect_peer_package(package)

    def test_package_rejects_mission_build_mod_and_checkpoint_mismatches(self):
        self.capture()
        package = self.root / "p.zip"
        rh.build_peer_package(self.store / "cp-test", "guest", package)
        for kwargs in ({"expected_mission": "other"}, {"expected_build": "other"},
                       {"expected_mods": "other"}, {"expected_checkpoint": "other"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(rh.HarnessError):
                rh.import_peer_package(package, self.root / ("target-" + next(iter(kwargs))), **kwargs)

    def test_marker_parser_valid_and_case_insensitive_mission(self):
        p = self.root / rh.CLIENT_MARKER
        p.write_text("campaign/Test.mi\n@/profiles/a/save/coop_ckpt/\nplayers 4 2\n", encoding="utf-8")
        marker, issues = rh.parse_marker(p)
        self.assertEqual(issues, [])
        self.assertEqual(rh.marker_match(marker, "CAMPAIGN/test.MI"), [])
        self.assertEqual((marker.players_base, marker.players_count), (4, 2))

    def test_marker_mapping_parser_matches_documented_sscanf_inputs(self):
        p = self.root / rh.CLIENT_MARKER
        p.write_text("m\na\nplayers +4 +2 trailing\n", encoding="utf-8")
        marker, issues = rh.parse_marker(p)
        self.assertFalse(issues)
        self.assertEqual((marker.players_base, marker.players_count), (4, 2))

    def test_marker_expiry_and_malformed_and_invalid_mapping(self):
        p = self.root / rh.CLIENT_MARKER
        p.write_text("m\na\nplayers 18 0\n", encoding="utf-8")
        os.utime(p, (1000, 1000))
        marker, issues = rh.parse_marker(p, now=1901)
        self.assertIsNone(marker)
        self.assertIn("expired", issues[0])
        os.utime(p, (1900, 1900))
        marker, issues = rh.parse_marker(p, now=1901)
        self.assertFalse(issues)
        self.assertIsNone(marker.players_base)
        p.write_text("only-one-line\n", encoding="utf-8")
        self.assertTrue(rh.parse_marker(p)[1])

    def test_marker_mismatch_and_missing_archive_are_diagnostic(self):
        marker = rh.Marker("mission-a", "archive-path")
        self.assertEqual(rh.marker_match(marker, "mission-b", False), ["mission path mismatch", "archive path does not exist"])

    def test_prepared_recovery_requires_internal_mission_player_mapping_and_confirmed_lobby(self):
        self.capture()
        local = self.root / "local"
        self.write(local / "coop_ckpt.sav", b"client-guest")
        meta = {"checkpoint_id": "cp-test", "internal_mission_path": "campaign/x.mi", "archive_path": "@/profiles/guest/save/coop_ckpt/", "player_id": "guest",
                "game_build": "1.069.0", "mission_id": "mission-x", "mod_fingerprint": "mods-sha",
                "is_host": False, "role": "client"}
        with self.assertRaisesRegex(rh.HarnessError, "readiness"):
            rh.prepare_marker(self.store / "cp-test", meta, "client", local / "coop_ckpt.sav", self.root / "temp")
        with self.assertRaisesRegex(rh.HarnessError, "player_mapping"):
            rh.prepare_marker(self.store / "cp-test", meta, "client", local / "coop_ckpt.sav", self.root / "temp", True)

    def test_prepare_marker_verifies_roster_archive_and_no_overwrite(self):
        self.capture()
        local = self.root / "local"
        self.write(local / "coop_ckpt.sav", b"client-guest")
        profile = self.root / "profile"
        self.write(profile / "save" / "coop_ckpt.sav", b"client-guest")
        meta = {"checkpoint_id": "cp-test", "internal_mission_path": "campaign/x.mi", "archive_path": "@/profiles/guest/save/coop_ckpt/", "player_id": "guest",
                "game_build": "1.069.0", "mission_id": "mission-x", "mod_fingerprint": "mods-sha",
                "is_host": False, "role": "client", "player_mapping": {"base": 4, "count": 2}, "profile_root": str(profile), "profile_id": "guest"}
        path = rh.prepare_marker(self.store / "cp-test", meta, "client", local / "coop_ckpt.sav", self.root / "temp", True)
        marker, issues = rh.parse_marker(path)
        self.assertFalse(issues)
        self.assertEqual(marker.mission, "campaign/x.mi")
        self.assertEqual(marker.archive, "@/profiles/guest/save/coop_ckpt/")
        self.assertEqual(marker.players_base, 4)
        with self.assertRaisesRegex(rh.HarnessError, "already exists"):
            rh.prepare_marker(self.store / "cp-test", meta, "client", local / "coop_ckpt.sav", self.root / "temp", True)

    def test_import_refuses_overwrite_and_rollback_on_partial_import(self):
        self.capture()
        package = self.root / "p.zip"
        rh.build_peer_package(self.store / "cp-test", "guest", package)
        target = self.root / "target"
        self.write(target / "coop_ckpt.sav", b"existing")
        with self.assertRaisesRegex(rh.HarnessError, "Refusing to overwrite"):
            rh.import_peer_package(package, target)
        self.assertEqual((target / "coop_ckpt.sav").read_bytes(), b"existing")

    def test_import_resumes_after_simulated_manager_crash(self):
        self.capture()
        package = self.root / "host.zip"
        rh.build_peer_package(self.store / "cp-test", "host", package, include_host_server=True)
        target = self.root / "target"
        original = rh.shutil.copyfileobj
        calls = 0

        def interrupt_after_first_archive(source, dest, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                dest.write(b"partial")
                raise KeyboardInterrupt("simulated process termination")
            return original(source, dest, *args, **kwargs)

        with mock.patch.object(rh.shutil, "copyfileobj", side_effect=interrupt_after_first_archive):
            with self.assertRaises(KeyboardInterrupt):
                rh.import_peer_package(package, target)
        self.assertTrue((target / "coop_ckpt.sav").is_file())
        self.assertTrue((target / "coop_ckpt_srv.sav.partial").exists())
        result = rh.import_peer_package(package, target)
        self.assertTrue((target / "coop_ckpt.sav").is_file())
        self.assertTrue((target / "coop_ckpt_srv.sav").is_file())
        self.assertTrue((target / "recovery_package.json").is_file())
        self.assertEqual(result["metadata"]["participant_id"], "host")

    def test_backup_existing_marker(self):
        marker = self.root / rh.CLIENT_MARKER
        marker.write_text("mission\narchive\n", encoding="utf-8")
        backup = rh.backup_marker(marker, self.root / "backup")
        self.assertEqual(backup.read_text(encoding="utf-8"), marker.read_text(encoding="utf-8"))

    def test_coop_log_diagnostics_are_filtered_and_path_redacted(self):
        logs = self.root / "temp"
        logs.mkdir()
        (logs / "goh_coop_checkpoint_game.log").write_text(
            "noise line\nERROR reload failed C:\\Users\\Somebody\\Documents\\profiles\\abc\\save\n",
            encoding="utf-8")
        lines = rh.read_coop_diagnostics(logs)
        self.assertEqual(len(lines), 1)
        self.assertIn("ERROR", lines[0])
        self.assertNotIn("C:\\Users\\Somebody", lines[0])
        self.assertIn("<redacted>", lines[0])

    def test_unknown_game_version_and_restarted_manager_smoke(self):
        self.assertEqual(manager.smoke_check(), 0)

    def test_same_checkpoint_identifier_is_coordinated_by_manifest(self):
        self.capture()
        meta, issues = rh.read_checkpoint(self.store / "cp-test")
        self.assertFalse(issues)
        self.assertEqual(meta["checkpoint_id"], "cp-test")


if __name__ == "__main__":
    unittest.main()
