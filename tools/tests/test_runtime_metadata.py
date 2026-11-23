"""Synthetic parser/filesystem tests; fixtures are not evidence of game operation."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from archive_path_resolver import ArchivePathResolver
from runtime_metadata import RuntimeMetadataCollector, MISSING, OBSERVED


class RuntimeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.collector = RuntimeMetadataCollector()

    def tearDown(self):
        self.temp.cleanup()

    def log(self, text):
        path = self.root / "goh_coop_checkpoint_call_to_arms.exe.log"
        path.write_text(text, encoding="utf-8")
        return path

    def test_parse_published_mission_checkpoint_clock_mapping_and_reload_path(self):
        p = self.log("""scene load: mission 'campaign/test.mi'
players: ids start after 4, 2 players (normal start of 'campaign/test.mi')
checkpoint #17: saving 'coop_ckpt' at quant clock 881
checkpoint written in 238 ms: @/profiles/765/save/coop_ckpt.sav
reload ARMED: next start of 'campaign/test.mi' loads '@/profiles/765/save/coop_ckpt/' (player ids after 4, 2 players)
""")
        d = self.collector.parse_log(p)
        self.assertEqual(d["internal_mission_path"].value, "campaign/test.mi")
        self.assertEqual(d["checkpoint_id"].value, 17)
        self.assertEqual(d["quant_clock"].value, 881)
        self.assertEqual(d["player_mapping"].value, {"base": 4, "count": 2})
        self.assertEqual(d["virtual_archive_base_path"].value, "@/profiles/765/save/coop_ckpt/")
        self.assertEqual(d["write_completion"].status, OBSERVED)
        self.assertTrue(all(value.source_path == str(p) and value.observed_at_utc and value.method for value in d.values()))

    def test_checkpoint_written_line_does_not_invent_virtual_base(self):
        d = self.collector.parse_log(self.log("checkpoint written in 17 ms: C:/profiles/12/save/coop_ckpt.sav\n"))
        self.assertIsNone(d["virtual_archive_base_path"].value)
        self.assertEqual(d["virtual_archive_base_path"].status, MISSING)

    def test_truncated_log_produces_missing_values(self):
        d = self.collector.parse_log(self.log("checkpoint #broken\nplayers: ids start after x"))
        self.assertEqual(d["checkpoint_id"].status, MISSING)
        self.assertEqual(d["player_mapping"].status, MISSING)

    def test_missing_log_is_missing(self):
        self.assertEqual(self.collector.parse_log(self.root / "absent.log")["internal_mission_path"].status, MISSING)

    def test_resolver_resolves_exact_client_file(self):
        profile = self.root / "profile"
        (profile / "save").mkdir(parents=True)
        f = profile / "save" / "coop_ckpt.sav"
        f.write_bytes(b"synthetic")
        result = ArchivePathResolver().resolve("@/profiles/123/save/coop_ckpt/", profile, "123", "client")
        self.assertEqual(result.status, "RESOLVED_LOCAL_FILE")
        self.assertEqual(result.local_file, f.resolve())

    def test_resolver_resolves_server_independently(self):
        profile = self.root / "profile"
        (profile / "save").mkdir(parents=True)
        (profile / "save" / "coop_ckpt_srv.sav").write_bytes(b"server")
        result = ArchivePathResolver().resolve("@/profiles/p/save/coop_ckpt_srv/", profile, "p", "server")
        self.assertEqual(result.status, "RESOLVED_LOCAL_FILE")

    def test_resolver_blocks_profile_mismatch(self):
        result = ArchivePathResolver().resolve("@/profiles/other/save/coop_ckpt/", self.root, "selected", "client")
        self.assertEqual(result.status, "UNVERIFIED_PATH")

    def test_resolver_blocks_role_stem_mismatch(self):
        result = ArchivePathResolver().resolve("@/profiles/p/save/coop_ckpt/", self.root, "p", "server")
        self.assertEqual(result.status, "UNVERIFIED_PATH")

    def test_resolver_blocks_unknown_syntax_and_windows_paths(self):
        for path in ("C:/save/coop_ckpt.sav", "@/profiles/p/save/coop_ckpt.sav", "@/profiles/p/save/other/"):
            with self.subTest(path=path):
                self.assertEqual(ArchivePathResolver().resolve(path, self.root, "p", "client").status, "UNVERIFIED_PATH")

    def test_resolver_blocks_absent_archive_without_searching(self):
        self.assertEqual(ArchivePathResolver().resolve("@/profiles/p/save/coop_ckpt/", self.root, "p", "client").status, "UNVERIFIED_PATH")

    def test_installation_probe_uses_binaries_x64_version_dll_and_distinguishes_states(self):
        game = self.root / "game"
        (game / "binaries/x64").mkdir(parents=True)
        (game / "binaries/x64/version.dll").write_bytes(b"synthetic loader placeholder")
        (game / "binaries/x64/goh_mod_loader.dll").write_bytes(b"synthetic loader placeholder")
        records = {r["component"]: r for r in self.collector.inspect_installation(game)}
        self.assertEqual(records["version_proxy"]["file_status"], "FILE_FOUND")
        self.assertEqual(records["version_proxy"]["loader_status"], "UNVERIFIED")
        self.assertEqual(records["goh_mod_loader"]["loader_status"], "LOADER_IDENTIFIED")
        self.assertEqual(records["goh_mod_loader"]["runtime_status"], "UNVERIFIED")
        self.assertEqual(records["coop_checkpoint_dll"]["file_status"], MISSING)

    def test_observe_copies_and_hashes_local_client_without_mutating_source(self):
        source = self.root / "coop_ckpt.sav"
        source.write_bytes(b"synthetic client checkpoint")
        before = source.read_bytes()
        result = self.collector.observe_save(source, "client", self.root / "store", stable_interval=.1, stable_samples=2)
        self.assertEqual(result.sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(Path(result.copy_path).read_bytes(), before)
        self.assertEqual(source.read_bytes(), before)
        self.assertIn("not engine synchronization proof", result.engine_write_confirmation)

    def test_observe_rejects_wrong_archive_name(self):
        f = self.root / "autosave.sav"
        f.write_bytes(b"x")
        with self.assertRaises(ValueError):
            self.collector.observe_save(f, "client", self.root / "store", stable_interval=.1, stable_samples=2)

    def test_observe_cancellation(self):
        f = self.root / "coop_ckpt.sav"
        f.write_bytes(b"x")
        stop = threading.Event(); stop.set()
        with self.assertRaises(InterruptedError):
            self.collector.observe_save(f, "client", self.root / "store", stable_interval=.1, stable_samples=3, cancel=stop)

    def test_profiles_scanned_only_one_level_and_known_filenames(self):
        (self.root / "p1" / "save").mkdir(parents=True)
        (self.root / "p1" / "save" / "coop_ckpt.sav").write_bytes(b"x")
        (self.root / "p2" / "save").mkdir(parents=True)
        (self.root / "p2" / "save" / "unrelated.sav").write_bytes(b"x")
        (self.root / "deep" / "x" / "save").mkdir(parents=True)
        (self.root / "deep" / "x" / "save" / "coop_ckpt.sav").write_bytes(b"x")
        self.assertEqual(self.collector.profile_candidates([self.root]), [(self.root / "p1").resolve()])

    def test_observer_rate_clamps_and_stops(self):
        f = self.root / "coop_ckpt.sav"; f.write_bytes(b"x")
        event = threading.Event(); event.set()
        # Initial stop is safe and returns without scanning recursively.
        self.collector.watch([f], lambda *_: self.fail("callback should not run"), event, interval=.01)

    def test_observer_detects_only_selected_files(self):
        f = self.root / "coop_ckpt.sav"; f.write_bytes(b"x")
        stop = threading.Event(); calls=[]
        thread = threading.Thread(target=self.collector.watch, args=([f], lambda *args: calls.append(args), stop, 2.0))
        thread.start(); time.sleep(.1); f.write_bytes(b"changed-size"); time.sleep(2.1); stop.set(); thread.join(1)
        self.assertEqual(len(calls), 1)

    def test_observer_detects_file_appearing_after_start(self):
        f = self.root / "later" / "coop_ckpt.sav"; f.parent.mkdir()
        stop = threading.Event(); calls=[]
        thread = threading.Thread(target=self.collector.watch, args=([f], lambda *args: calls.append(args), stop, 2.0))
        thread.start(); time.sleep(.1); f.write_bytes(b"synthetic"); time.sleep(2.1); stop.set(); thread.join(1)
        self.assertEqual(len(calls), 1)

    def test_marker_metadata_provenance_and_expiry(self):
        p = self.root / "coop_checkpoint_reload_client.txt"
        p.write_text("campaign/a.mi\n@/profiles/p/save/coop_ckpt/\nplayers 4 2\n", encoding="utf-8")
        data = self.collector.marker_metadata(p)
        self.assertEqual(data["internal_mission_path"].value, "campaign/a.mi")
        self.assertEqual(data["internal_mission_path"].source_path, str(p))
        os.utime(p, (time.time() - 1000, time.time() - 1000))
        self.assertEqual(self.collector.marker_metadata(p)["internal_mission_path"].status, MISSING)

    def test_log_does_not_claim_session_role_or_game_build(self):
        d = self.collector.parse_log(self.log("scene load: mission 'campaign/test.mi'\n"))
        self.assertEqual(d["session_role"].status, MISSING)
        self.assertEqual(d["game_build"].status, MISSING)

    def test_role_log_selection_requires_exact_role_and_rejects_ambiguity(self):
        client = self.root / "goh_coop_checkpoint_call_to_arms.exe.log"
        server = self.root / "goh_coop_checkpoint_call_to_arms_server.exe.log"
        client.write_text("client", encoding="utf-8")
        server.write_text("server", encoding="utf-8")
        self.assertEqual(self.collector.select_role_log([client, server], "client"), client)
        self.assertEqual(self.collector.select_role_log([client, server], "server"), server)
        with self.assertRaises(ValueError):
            self.collector.select_role_log([client], "server")
        duplicate = self.root / "other" / client.name
        duplicate.parent.mkdir(); duplicate.write_text("other", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.collector.select_role_log([client, duplicate], "client")


if __name__ == "__main__":
    unittest.main()
