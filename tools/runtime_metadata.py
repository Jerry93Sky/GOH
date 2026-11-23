#!/usr/bin/env python3
"""Read-only, provenance-carrying observation of Coop Checkpoint files and logs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Iterable

import recovery_harness as harness
from coop_log_sources import classify_log_role, select_role_log, sha256_file

OBSERVED = "OBSERVED"
OPERATOR_PROVIDED = "OPERATOR_PROVIDED"
UNVERIFIED = "UNVERIFIED"
MISSING = "MISSING"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class FieldValue:
    value: Any
    status: str
    source_path: str | None
    observed_at_utc: str
    method: str
    process_role: str | None = None


@dataclass(frozen=True)
class SaveSnapshot:
    source_path: str
    role: str
    observed_at_utc: str
    size_bytes: int
    sha256: str
    copy_path: str | None
    copy_status: str
    engine_write_confirmation: str
    metadata: dict[str, FieldValue]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "metadata": {k: asdict(v) for k, v in self.metadata.items()}}


class RuntimeMetadataCollector:
    """Parses only published log phrases and observes exact, user-selected directories."""

    _MISSION = re.compile(r"scene load: mission '([^']+)'")
    _CHECKPOINT = re.compile(r"checkpoint #(\d+): saving '([^']+)' at quant clock (-?\d+)")
    _WRITTEN = re.compile(r"checkpoint written in (\d+) ms: (.+)")
    _PLAYERS = re.compile(r"players: ids start after (\d+), (\d+) players \(normal start of '([^']+)'\)")
    _RELOAD = re.compile(r"reload ARMED: next start of '([^']+)' loads '([^']+)' \(player ids after (\d+), (\d+) players\)")

    def _field(self, value: Any, status: str, source: Path | None, method: str,
               process_role: str | None = None) -> FieldValue:
        return FieldValue(value, status, str(source) if source else None, utc_now(), method, process_role)

    @staticmethod
    def classify_log_role(log_path: str | Path) -> str | None:
        return classify_log_role(log_path)

    def select_role_log(self, log_paths: Iterable[str | Path], role: str) -> Path:
        return select_role_log(list(log_paths), role)

    def parse_log(self, log_path: str | Path) -> dict[str, FieldValue]:
        path = Path(log_path)
        process_role = classify_log_role(path)
        result: dict[str, FieldValue] = {}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            role_status = process_role or "UNVERIFIED"
            result = {key: self._field(None, MISSING, path, "unreadable log", role_status) for key in
                      ("internal_mission_path", "virtual_archive_base_path", "checkpoint_id", "quant_clock", "player_mapping", "write_completion")}
            result["process_role"] = self._field(process_role, OBSERVED if process_role else UNVERIFIED,
                path, "exact log basename", role_status)
            result["session_role"] = self._field(None, MISSING, path,
                "process log cannot establish network host/client", role_status)
            result["game_build"] = self._field(None, MISSING, path, "not present in published log contract", role_status)
            result["source_log_sha256"] = self._field(None, UNVERIFIED, path, "unreadable log", role_status)
            return result
        def latest(pattern: re.Pattern[str]):
            for line in reversed(lines):
                found = pattern.search(line)
                if found:
                    return found
            return None
        mission = latest(self._MISSION)
        cp = latest(self._CHECKPOINT)
        written = latest(self._WRITTEN)
        players = latest(self._PLAYERS)
        reload = latest(self._RELOAD)
        role_status = process_role or "UNVERIFIED"
        result["internal_mission_path"] = self._field(mission.group(1) if mission else None, OBSERVED if mission else MISSING, path, "published Coop Checkpoint log phrase", role_status)
        # The published source stores this in process memory but does not log it as the marker archive base.
        # The written line is a local save path and is deliberately not relabeled as the virtual path.
        result["virtual_archive_base_path"] = self._field(reload.group(2) if reload else None,
            OBSERVED if reload else MISSING, path,
            "published reload ARMED marker line; checkpoint written line alone is only a local save path", role_status)
        result["checkpoint_id"] = self._field(int(cp.group(1)) if cp else None, OBSERVED if cp else MISSING, path, "local process checkpoint sequence; not a cross-peer UUID", role_status)
        result["quant_clock"] = self._field(int(cp.group(3)) if cp else None, OBSERVED if cp else MISSING, path, "published checkpoint log quant clock", role_status)
        mapping = None
        if players:
            mapping = {"base": int(players.group(1)), "count": int(players.group(2))}
        elif reload:
            mapping = {"base": int(reload.group(3)), "count": int(reload.group(4))}
        result["player_mapping"] = self._field(mapping, OBSERVED if mapping else MISSING, path, "published players/reload log phrase", role_status)
        result["write_completion"] = self._field(written.group(0) if written else None,
            OBSERVED if written else UNVERIFIED, path,
            "author log phrase indicates local save completion only; no multi-peer synchronization ACK", role_status)
        result["process_role"] = self._field(process_role, OBSERVED if process_role else UNVERIFIED,
            path, "exact pinned Coop Checkpoint log basename identifies game or local server executable", role_status)
        result["session_role"] = self._field(None, MISSING, path,
            "game executable log does not distinguish host game from guest client", role_status)
        result["game_build"] = self._field(None, MISSING, path, "not present in parsed published log contract", role_status)
        result["source_log_sha256"] = self._field(sha256_file(path), OBSERVED if process_role else UNVERIFIED,
            path, "SHA-256 of exact log source", role_status)
        return result

    def marker_metadata(self, marker_path: str | Path) -> dict[str, FieldValue]:
        p = Path(marker_path)
        marker, issues = harness.parse_marker(p)
        if issues or marker is None:
            return {"internal_mission_path": self._field(None, MISSING, p, "; ".join(issues)),
                    "virtual_archive_base_path": self._field(None, MISSING, p, "; ".join(issues)),
                    "player_mapping": self._field(None, MISSING, p, "; ".join(issues))}
        return {
            "internal_mission_path": self._field(marker.mission, OBSERVED, p, "read existing marker line 1"),
            "virtual_archive_base_path": self._field(marker.archive, OBSERVED, p, "read existing marker line 2"),
            "player_mapping": self._field({"base": marker.players_base, "count": marker.players_count} if marker.players_base is not None else None,
                OBSERVED if marker.players_base is not None else MISSING, p, "read existing marker players line"),
        }

    def observe_save(self, source: str | Path, role: str, store_dir: str | Path,
                     metadata: dict[str, FieldValue] | None = None,
                     stable_interval: float = 2.0, stable_samples: int = 3,
                     cancel=None) -> SaveSnapshot:
        """Wait for stable metadata, then copy/hash with race checks. Stability is never engine ACK."""
        path = Path(source).resolve(strict=True)
        if role not in {"client", "server"} or path.name.casefold() != ("coop_ckpt.sav" if role == "client" else "coop_ckpt_srv.sav"):
            raise ValueError("Only exact Coop Checkpoint client/server archive filenames are observed")
        prior = None
        stable = 0
        while stable < max(2, min(stable_samples, 8)):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("observation cancelled")
            st = path.stat()
            current = (st.st_size, st.st_mtime_ns)
            stable = stable + 1 if current == prior else 1
            prior = current
            if stable < max(2, min(stable_samples, 8)):
                time.sleep(max(0.1, min(stable_interval, 30.0)))
        before = path.stat()
        digest = self._digest(path)
        store = Path(store_dir).resolve()
        store.mkdir(parents=True, exist_ok=True)
        dest_dir = store / f"local-{role}-{before.st_mtime_ns}-{before.st_size}-{time.time_ns()}"
        dest_dir.mkdir(exist_ok=False)
        copy = dest_dir / path.name
        try:
            shutil.copy2(path, copy)
            after = path.stat()
            if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                    or self._digest(copy) != digest or self._digest(path) != digest):
                raise RuntimeError("source changed during copy; incomplete observation discarded")
            return SaveSnapshot(str(path), role, utc_now(), before.st_size, digest, str(copy), "LOCAL_COPY_VERIFIED",
                "UNVERIFIED: stable size/mtime is not engine synchronization proof", metadata or {})
        except Exception:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise

    @staticmethod
    def _digest(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    @staticmethod
    def inspect_installation(game_root: str | Path) -> list[dict[str, Any]]:
        """Inventory known paths without treating presence as runtime compatibility."""
        root = Path(game_root)
        entries = [
            ("game_executable", root / "binaries/x64/call_to_arms.exe", False),
            ("server_executable", root / "binaries/x64/call_to_arms_server.exe", False),
            ("version_proxy", root / "binaries/x64/version.dll", False),
            ("coop_checkpoint_dll", root / "binaries/x64/goh_mods/coop_checkpoint.dll", False),
            ("coop_checkpoint_data", root / "mods/coop_checkpoint", False),
            ("goh_mod_loader", root / "binaries/x64/goh_mod_loader.dll", True),
            ("coa_extender", root / "binaries/x64/coa_extender.dll", True),
        ]
        result = []
        for name, path, known_loader in entries:
            found = path.exists()
            record: dict[str, Any] = {"component": name, "path": str(path),
                "file_status": "FILE_FOUND" if found else MISSING,
                "loader_status": "LOADER_IDENTIFIED" if found and known_loader else UNVERIFIED,
                "runtime_status": UNVERIFIED}
            if path.is_file():
                try:
                    record["sha256"] = RuntimeMetadataCollector._digest(path)
                except OSError:
                    record["sha256"] = None
                    record["hash_status"] = UNVERIFIED
            result.append(record)
        return result

    def profile_candidates(self, roots: Iterable[str | Path]) -> list[Path]:
        """Inspect only selected roots for profile directories containing save/coop_ckpt*.sav."""
        found = set()
        for root in roots:
            p = Path(root)
            if not p.is_dir():
                continue
            try:
                for profile in p.iterdir():
                    save = profile / "save"
                    if profile.is_dir() and ((save / "coop_ckpt.sav").is_file() or (save / "coop_ckpt_srv.sav").is_file()):
                        found.add(profile.resolve())
            except OSError:
                continue
        return sorted(found)

    def watch(self, paths: Iterable[str | Path], callback, stop_event, interval: float = 3.0) -> None:
        """Bounded polling over explicitly selected files only; interval clamps to >=2 seconds."""
        interval = max(2.0, min(float(interval), 60.0))
        tracked = [Path(p).resolve() for p in paths]
        signatures: dict[Path, tuple[int, int] | None | bool] = {p: False for p in tracked}
        while not stop_event.is_set():
            for path in tracked:
                try:
                    stat = path.stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    signature = None
                if signature is not None and signatures[path] is not False and signatures[path] != signature:
                    callback(path, signature)
                signatures[path] = signature
            stop_event.wait(interval)
