from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
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

    def _record_test(self, checkpoint_id, run_id, test_id, status):
        evidence = self.base / f"{run_id}-evidence.txt"
        evidence.write_text("synthetic protocol fixture", encoding="utf-8")
        outcome = "PASS" if status == "RESTORE_VERIFIED" else "FAIL"
        protocol = self.base / f"{run_id}.json"
        protocol.write_text(json.dumps({
            "experiment_type": "real_game", "checkpoint_id": checkpoint_id,
            "test_run_id": run_id, "test_id": test_id, "executed_at_utc": "2026-11-23T12:00:00Z",
            "game_build": "build-x", "participant_ids": ["host", "guest"],
            "outcome": outcome, "evidence_files": [evidence.name],
        }), encoding="utf-8")
        args = type("Args", (), {
            "store": str(self.store), "checkpoint_id": checkpoint_id,
            "test_run_id": run_id, "test_id": test_id, "status": status,
            "protocol": str(protocol), "confirm_real_game": True,
        })()
        return rc.record_result(args)

    def test_complete_capture_and_restore_plan_remains_pending(self):
        captured = self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertEqual(captured["status"], "FILE_VERIFIED")
        self.assertEqual(len(captured["files"]), 3)
        plan = self._plan()
        self.assertEqual(plan["status"], "RESTORE_TEST_PENDING")
        self.assertEqual(plan["checkpoint_id"], "cp-1")
        self.assertEqual(plan["latest_physically_available_checkpoint_id"], "cp-1")
        self.assertIsNone(plan["latest_game_test_verified_checkpoint_id"])
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

    def test_ambiguous_source_is_refused_with_diagnostic_status(self):
        self._write(self.host / "nested" / "coop_ckpt.sav", b"duplicate client archive")
        captured = self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertEqual(captured["status"], "INCOMPLETE")
        self.assertEqual(captured["diagnostic_status"], "SOURCE_AMBIGUOUS")
        self.assertEqual(self._plan()["status"], "INCOMPLETE")

    def test_source_change_during_copy_is_recorded_and_excluded(self):
        original_copy = rc.shutil.copyfile

        def copy_then_mutate(source, target):
            original_copy(source, target)
            if Path(source) == self.host / "coop_ckpt.sav":
                Path(source).write_bytes(b"changed-during-copy")
            return Path(target)

        with mock.patch.object(rc.shutil, "copyfile", side_effect=copy_then_mutate):
            captured = self._capture(host=self.host, guest=self.guest, server=self.server)
        self.assertEqual(captured["status"], "SOURCE_UNSTABLE")
        self.assertEqual(captured["diagnostic_status"], "SOURCE_UNSTABLE")
        self.assertFalse(captured["files"][0]["source_stable_during_copy"])
        self.assertEqual(self._plan()["status"], "INCOMPLETE")

    def test_capture_error_gets_separate_diagnostic_report(self):
        argv = [
            "capture", "--store", str(self.store), "--checkpoint-id", "cp-error",
            "--capture-run-id", "capture-error-1", "--checkpoint-time-utc", "2026-11-23T12:00:00Z",
            "--mission-id", "mission-x", "--game-build", "build-x", "--mod-fingerprint", "mods-x",
            "--expected-player", "host", "--expected-player", "guest",
            "--peer", f"host={self.host}", "--peer", f"guest={self.guest}", "--server-root", str(self.server),
        ]
        output = StringIO()
        with mock.patch.object(rc.shutil, "copyfile", side_effect=OSError("simulated read error")), redirect_stdout(output):
            code = rc.main(argv)
        self.assertEqual(code, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "CAPTURE_ERROR")
        self.assertFalse((self.store / "cp-error").exists())
        self.assertTrue(Path(report["diagnostic_report"]).is_file())

    def test_interrupted_copy_never_publishes_visible_checkpoint(self):
        self.store.mkdir(parents=True)
        with mock.patch.object(rc.shutil, "copyfile", side_effect=OSError("simulated interruption")):
            with self.assertRaises(rc.CaptureError):
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

    def test_plan_separates_latest_available_from_test_verified_checkpoint(self):
        self._capture("cp-tested", host=self.host, guest=self.guest, server=self.server,
                      checkpoint_time="2026-11-23T11:00:00Z")
        self._record_test("cp-tested", "run-b-pass", "TEST-B", "RESTORE_VERIFIED")
        self._capture("cp-available", host=self.host, guest=self.guest, server=self.server,
                      checkpoint_time="2026-11-23T13:00:00Z")
        plan = self._plan()
        self.assertEqual(plan["latest_physically_available_checkpoint_id"], "cp-available")
        self.assertEqual(plan["latest_game_test_verified_checkpoint_id"], "cp-tested")
        self.assertEqual(plan["latest_TEST_B_verified_checkpoint_id"], "cp-tested")
        self.assertEqual(plan["checkpoint_id"], "cp-available")

    def test_duplicate_plan_is_idempotent_and_conflicting_reuse_fails(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        first = self._plan()
        second = self._plan()
        self.assertEqual(first, second)
        with self.assertRaisesRegex(rc.CoordinatorError, "different inputs"):
            self._plan(players=["host", "other"])

    def test_expired_recovery_plan_is_returned_stale_without_mutation(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        original = self._plan()
        path = self.store / "plans" / "request-1.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["expires_at_utc"] = "2000-01-01T00:00:00Z"
        plan["plan_sha256"] = rc.plan_digest(plan)
        path.write_text(json.dumps(plan), encoding="utf-8")
        before = path.read_bytes()
        refreshed = self._plan()
        self.assertEqual(refreshed["status"], "STALE_PLAN")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(original["status"], "RESTORE_TEST_PENDING")

    def test_newer_checkpoint_stales_existing_plan_without_mutating_it(self):
        self._capture("cp-old", host=self.host, guest=self.guest, server=self.server,
                      checkpoint_time="2026-11-23T11:00:00Z")
        self._plan()
        path = self.store / "plans" / "request-1.json"
        before = path.read_bytes()
        self._capture("cp-new", host=self.host, guest=self.guest, server=self.server,
                      checkpoint_time="2026-11-23T13:00:00Z")
        self.assertEqual(self._plan()["status"], "STALE_PLAN")
        self.assertEqual(path.read_bytes(), before)

    def test_result_history_change_stales_existing_plan(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        self._plan()
        self._record_test("cp-1", "run-a-pass", "TEST-A", "RESTORE_VERIFIED")
        self.assertEqual(self._plan()["status"], "STALE_PLAN")

    def test_legacy_v1_manifest_remains_readable(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        path = self.store / "cp-1" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["schema_version"] = 1
        manifest.pop("diagnostic_status", None)
        manifest.pop("capture_run_id", None)
        manifest.pop("checkpoint_time_utc", None)
        for item in manifest["files"]:
            item.pop("source_before", None)
            item.pop("source_after", None)
            item.pop("source_stable_during_copy", None)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        plan = self._plan()
        self.assertEqual(plan["status"], "RESTORE_TEST_PENDING")
        self.assertEqual(plan["checkpoint_id"], "cp-1")

    def test_roster_mismatch_is_not_compatible(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        plan = self._plan(players=["host", "other"])
        self.assertEqual(plan["status"], "INCOMPLETE")
        self.assertIn("expected participant roster differs", json.dumps(plan["excluded_candidates"]))

    def test_restore_verified_requires_real_game_protocol(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        result_args = type("Args", (), {
            "store": str(self.store), "checkpoint_id": "cp-1",
            "test_run_id": "run-b-001", "test_id": "TEST-B",
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
            "experiment_type": "real_game", "checkpoint_id": "cp-1", "test_id": "TEST-B", "test_run_id": "run-b-001",
            "executed_at_utc": "2026-11-23T12:00:00Z", "game_build": "build-x",
            "participant_ids": ["host", "guest"], "outcome": "PASS",
            "evidence_files": ["evidence.txt"],
        }), encoding="utf-8")
        result_args.protocol = str(protocol_path)
        result = rc.record_result(result_args)
        self.assertEqual(result["status"], "RESTORE_VERIFIED")
        self.assertTrue((self.store / "results" / "runs" / "run-b-001.json").exists())

    def test_independent_test_runs_are_append_only_and_retryable(self):
        self._capture(host=self.host, guest=self.guest, server=self.server)
        args = type("Args", (), {
            "store": str(self.store), "checkpoint_id": "cp-1", "test_run_id": "test-a-fail",
            "test_id": "TEST-A", "status": "RESTORE_FAILED", "confirm_real_game": True,
            "protocol": str(self.base / "protocol-a.json"),
        })()

        args.protocol = str(self._protocol_fixture("test-a-fail", "TEST-A", "FAIL"))
        failed = rc.record_result(args)
        args.test_run_id = "test-a-retry"
        args.status = "RESTORE_VERIFIED"
        args.protocol = str(self._protocol_fixture("test-a-retry", "TEST-A", "PASS"))
        passed = rc.record_result(args)
        self.assertEqual(failed["status"], "RESTORE_FAILED")
        self.assertEqual(passed["status"], "RESTORE_VERIFIED")
        self.assertTrue((self.store / "results" / "runs" / "test-a-fail.json").exists())
        self.assertTrue((self.store / "results" / "runs" / "test-a-retry.json").exists())
        args.test_run_id = "test-a-retry"
        self.assertEqual(rc.record_result(args)["status"], "RESTORE_VERIFIED")
        args.status = "RESTORE_FAILED"
        args.protocol = str(self._protocol_fixture("test-a-retry", "TEST-A", "FAIL"))
        with self.assertRaisesRegex(rc.CoordinatorError, "history is immutable"):
            rc.record_result(args)

    def _protocol_fixture(self, run_id, test_id, outcome):
        evidence = self.base / f"{run_id}-evidence.txt"
        evidence.write_text("synthetic fixture", encoding="utf-8")
        path = self.base / f"{run_id}.json"
        path.write_text(json.dumps({
            "experiment_type": "real_game", "checkpoint_id": "cp-1", "test_run_id": run_id,
            "test_id": test_id, "executed_at_utc": "2026-11-23T12:00:00Z",
            "game_build": "build-x", "participant_ids": ["host", "guest"],
            "outcome": outcome, "evidence_files": [evidence.name],
        }), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
