from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recovery_coordinator as rc


class RecoveryCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = self.base / "store"
        self.host = self.base / "source-host"
        self.guest = self.base / "source-guest"
        self.server = self.base / "source-server"
        self._write(self.host / "coop_ckpt.sav", b"host-client-save")
        self._write(self.guest / "coop_ckpt.sav", b"guest-client-save")
        self._write(self.server / "coop_ckpt_srv.sav", b"host-server-save")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _capture(self, checkpoint_id="cp-1", *, host=None, guest=None, server=None, checkpoint_time="2026-11-23T12:00:00Z"):
        peers = []
        if host is not None:
            peers.append(f"host={host}")
        if guest is not None:
            peers.append(f"guest={guest}")
        args = type("Args", (), {
            "store": str(self.store),
            "checkpoint_id": checkpoint_id,
            "checkpoint_time_utc": checkpoint_time,
            "mission_id": "mission-x",
            "game_build": "build-x",
            "mod_fingerprint": "mods-x",
            "expected_player": ["host", "guest"],
            "peer": peers,
            "server_root": str(server) if server is not None else None,
        })()
        return rc.capture(args)

    def _plan(self, request_id="request-1", players=None):
        args = type("Args", (), {
            "store": str(self.store),
            "request_id": request_id,
            "mission_id": "mission-x",
            "game_build": "build-x",
            "mod_fingerprint": "mods-x",
            "expected_player": players or ["host", "guest"],
        })()
        return rc.plan_recovery(args)

    def test_complete_capture_and_restore_plan_remains_pending(self):
        captured = self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertEqual(captured["status"], "FILE_VERIFIED")
        self.assertEqual(len(captured["files"]), 3)
        plan = self._plan()
        self.assertEqual(plan["status"], "RESTORE_TEST_PENDING")
        self.assertEqual(plan["checkpoint_id"], "cp-1")
        self.assertEqual(len(plan["archives"]), 3)
        self.assertTrue(any("does not prove" in x for x in plan["diagnostics"]))

    def test_missing_peer_snapshot_is_incomplete_and_not_selected(self):
        captured = self._capture(host=self.host, guest=self.guest, server=None)
        self.assertEqual(captured["status"], "INCOMPLETE")
        self.assertIn("host_server/coop_ckpt_srv.sav", captured["missing"])
        self.assertEqual(self._plan()["status"], "INCOMPLETE")

    def test_ignores_unrelated_profile_saves(self):
        self._write(self.host / "campaign-save.sav", b"private unrelated save")
        captured = self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertEqual(captured["status"], "FILE_VERIFIED")
        self.assertEqual(len(captured["files"]), 3)
        paths = [item["path"] for item in captured["files"]]
        self.assertFalse(any("campaign-save" in path for path in paths))

    def test_interrupted_copy_never_publishes_visible_checkpoint(self):
        self.store.mkdir(parents=True)
        with mock.patch.object(rc.shutil, "copyfile", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertFalse((self.store / "cp-1").exists())
        self.assertTrue(list(self.store.glob(".cp-1.incomplete-*")))
        self.assertEqual(self._plan()["status"], "INCOMPLETE")
        retried = self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertEqual(retried["status"], "FILE_VERIFIED")

    def test_interrupted_manifest_write_is_not_published(self):
        self.store.mkdir(parents=True)
        with mock.patch.object(rc, "write_json_fsynced", side_effect=OSError("simulated manifest interruption")):
            with self.assertRaisesRegex(OSError, "manifest interruption"):
                self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertFalse((self.store / "cp-1").exists())
        stages = list(self.store.glob(".cp-1.incomplete-*"))
        self.assertEqual(len(stages), 1)
        self.assertFalse((stages[0] / "manifest.json").exists())

    def test_corrupt_copied_archive_is_excluded_from_plan(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        archive = self.store / "cp-1" / "data" / "participants" / "host" / "coop_ckpt.sav"
        archive.write_bytes(b"tampered")
        plan = self._plan()
        self.assertEqual(plan["status"], "INCOMPLETE")
        reasons = json.dumps(plan["excluded_candidates"])
        self.assertIn("SHA-256 mismatch", reasons)

    def test_duplicate_capture_id_fails_without_overwriting_first(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        manifest_path = self.store / "cp-1" / "manifest.json"
        before = manifest_path.read_bytes()
        with self.assertRaisesRegex(rc.CoordinatorError, "already exists"):
            self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_plan_chooses_latest_game_checkpoint_time(self):
        self._capture("cp-later-packaged", host=self.host, guest=self.guest, server=self.server,
                      checkpoint_time="2026-11-23T11:00:00Z")
        self._capture("cp-latest-game-time", host=self.host, guest=self.guest, server=self.server,
                      checkpoint_time="2026-11-23T13:00:00Z")
        self.assertEqual(self._plan()["checkpoint_id"], "cp-latest-game-time")

    def test_duplicate_plan_is_idempotent_and_conflicting_reuse_fails(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        first = self._plan()
        second = self._plan()
        self.assertEqual(first, second)
        with self.assertRaisesRegex(rc.CoordinatorError, "different inputs"):
            self._plan(players=["host", "other"])

    def test_roster_mismatch_is_not_compatible(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        plan = self._plan(players=["host", "other"])
        self.assertEqual(plan["status"], "INCOMPLETE")
        self.assertIn("expected participant roster differs", json.dumps(plan["excluded_candidates"]))

    def test_restore_verified_requires_real_game_protocol(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        result_args = type("Args", (), {
            "store": str(self.store), "checkpoint_id": "cp-1",
            "status": "RESTORE_VERIFIED", "protocol": str(self.base / "missing.json"),
            "confirm_real_game": False,
        })()
        with self.assertRaisesRegex(rc.CoordinatorError, "Explicit --confirm-real-game"):
            rc.record_result(result_args)
        result_args.confirm_real_game = True
        with self.assertRaisesRegex(rc.CoordinatorError, "Could not read"):
            rc.record_result(result_args)
        protocol_path = self.base / "protocol.json"
        evidence_path = self.base / "evidence.txt"
        evidence_path.write_text("synthetic test fixture only", encoding="utf-8")
        protocol_path.write_text(json.dumps({
            "experiment_type": "real_game", "checkpoint_id": "cp-1", "test_id": "TEST-B",
            "executed_at_utc": "2026-11-23T12:00:00Z", "game_build": "build-x",
            "participant_ids": ["host", "guest"], "outcome": "PASS",
            "evidence_files": ["evidence.txt"],
        }), encoding="utf-8")
        result_args.protocol = str(protocol_path)
        result = rc.record_result(result_args)
        self.assertEqual(result["status"], "RESTORE_VERIFIED")
        self.assertTrue((self.store / "results" / "cp-1.json").exists())


if __name__ == "__main__":
    unittest.main()
