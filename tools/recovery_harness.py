#!/usr/bin/env python3
"""Independent filesystem-only recovery package and Coop Checkpoint marker harness.

The marker simulator models the published text marker contract. It is not the game loader.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import recovery_coordinator as coordinator
from archive_path_resolver import ArchivePathResolver
from coop_log_sources import classify_log_role, sha256_file

MARKER_TTL_SECONDS = 15 * 60
CLIENT_MARKER = "coop_checkpoint_reload_client.txt"
SERVER_MARKER = "coop_checkpoint_reload_server.txt"
PACKAGE_FORMAT = "goh-coop-recovery-peer-package-v1"


class HarnessError(ValueError):
    pass


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def read_checkpoint(checkpoint_dir: str | Path) -> tuple[dict[str, Any], list[str]]:
    directory = Path(checkpoint_dir).resolve()
    manifest = coordinator.load_manifest(directory)
    issues = coordinator.verify_manifest_files(directory, manifest) + coordinator.structure_issues(manifest)
    return manifest, issues


def build_peer_package(checkpoint_dir: str | Path, player_id: str, destination: str | Path,
                       include_host_server: bool = False) -> Path:
    """Export only the chosen participant save; host can additionally receive local server save."""
    checkpoint = Path(checkpoint_dir).resolve()
    manifest, issues = read_checkpoint(checkpoint)
    if issues:
        raise HarnessError("Checkpoint verification failed: " + "; ".join(issues))
    if player_id not in manifest.get("expected_players", []):
        raise HarnessError(f"Unknown participant: {player_id}")
    selected = [f for f in manifest["files"] if f.get("owner") == f"participant:{player_id}"]
    if include_host_server:
        selected += [f for f in manifest["files"] if f.get("owner") == "host_server"]
    if len(selected) != (2 if include_host_server else 1):
        raise HarnessError("Checkpoint is missing one or more required archive records")

    dest = Path(destination).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise HarnessError(f"Refusing to overwrite package: {dest}")
    package_manifest = {
        "format": PACKAGE_FORMAT,
        "checkpoint_id": manifest["checkpoint_id"],
        "participant_id": player_id,
        "includes_host_server": bool(include_host_server),
        "mission_id": manifest["mission_id"],
        "game_build": manifest["game_build"],
        "mod_fingerprint": manifest["mod_fingerprint"],
        "checkpoint_time_utc": manifest["checkpoint_time_utc"],
        "expected_players": manifest["expected_players"],
        "files": [],
    }
    stage = Path(tempfile.mkdtemp(prefix="goh-package-", dir=dest.parent))
    try:
        (stage / "archives").mkdir()
        for i, record in enumerate(selected):
            source_rel = PurePosixPath(record["path"])
            if source_rel.is_absolute() or ".." in source_rel.parts:
                raise HarnessError("Unsafe archive path in checkpoint manifest")
            source = (checkpoint / Path(*source_rel.parts)).resolve(strict=True)
            if checkpoint not in source.parents:
                raise HarnessError("Archive escaped checkpoint directory")
            filename = "client.sav" if record["owner"].startswith("participant:") else "server.sav"
            target = stage / "archives" / filename
            shutil.copyfile(source, target)
            package_manifest["files"].append({
                "role": "client" if filename == "client.sav" else "server",
                "path": f"archives/{filename}",
                "size_bytes": target.stat().st_size,
                "sha256": _digest(target),
            })
        _write_json(stage / "package.json", package_manifest)
        # Keep the package manifest metadata available beside locally imported archives.
        with zipfile.ZipFile(dest, "x", compression=zipfile.ZIP_DEFLATED) as z:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    z.write(path, path.relative_to(stage).as_posix())
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def inspect_peer_package(package: str | Path) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    with zipfile.ZipFile(package) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise HarnessError("Package contains duplicate paths")
        if set(names) != {"package.json", "archives/client.sav", *({"archives/server.sav"} if "archives/server.sav" in names else set())}:
            raise HarnessError("Package contains unexpected or missing entries")
        meta = json.loads(z.read("package.json").decode("utf-8"))
        if meta.get("format") != PACKAGE_FORMAT:
            raise HarnessError("Unsupported recovery package format")
        files = meta.get("files")
        if not isinstance(files, list):
            raise HarnessError("Package file list is malformed")
        expected_names = {item.get("path") for item in files}
        if expected_names != set(names) - {"package.json"}:
            raise HarnessError("Package inventory differs from archive contents")
        for item in files:
            data = z.read(item["path"])
            if len(data) != item.get("size_bytes"):
                issues.append(f"size mismatch: {item['path']}")
            if hashlib.sha256(data).hexdigest() != item.get("sha256"):
                issues.append(f"SHA-256 mismatch: {item['path']}")
        roles = {item.get("role") for item in files}
        if roles not in ({"client"}, {"client", "server"}):
            issues.append("client archive is required; duplicate/unknown role")
        if bool(meta.get("includes_host_server")) != ("server" in roles):
            issues.append("host server flag does not match package contents")
        players = meta.get("expected_players")
        if not isinstance(players, list) or meta.get("participant_id") not in players or len(set(players)) < 2:
            issues.append("participant identity does not match roster")
        for field in ("checkpoint_id", "mission_id", "game_build", "mod_fingerprint"):
            if not isinstance(meta.get(field), str) or not meta[field].strip():
                issues.append(f"missing package metadata: {field}")
        return meta, issues


def validate_consistency_protocol(protocol: dict[str, Any], expected_players: list[str],
                                  mission_id: str, package_checkpoint_id: str | None = None) -> tuple[bool, list[str]]:
    """Validate operator-recorded cross-peer checkpoint evidence, without claiming sync proof."""
    issues: list[str] = []
    if protocol.get("schema") != "goh-checkpoint-consistency-protocol-v1":
        issues.append("unsupported or missing manual checkpoint protocol schema")
    if not protocol.get("operator_confirmed_same_checkpoint_event"):
        issues.append("operator has not recorded that peers observed the same checkpoint event")
    if protocol.get("mission_id") != mission_id:
        issues.append("manual protocol mission does not match recovery package")
    checkpoint_id = protocol.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
        issues.append("manual protocol has no operator-assigned shared checkpoint label")
    if package_checkpoint_id is not None and protocol.get("package_checkpoint_id") != package_checkpoint_id:
        issues.append("manual protocol package checkpoint reference does not match selected recovery files")
    records = protocol.get("participants")
    if not isinstance(records, list):
        return False, issues + ["manual protocol participant sources are missing"]
    seen: set[tuple[str, str]] = set()
    mission_sources: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            issues.append("manual protocol contains a malformed participant source")
            continue
        pid, role = item.get("participant_id"), item.get("process_role")
        if pid not in expected_players or role not in {"client", "server"}:
            issues.append(f"manual protocol contains an unknown participant/process role: {pid}/{role}")
        key = (str(pid), str(role))
        if key in seen:
            issues.append(f"duplicate manual protocol source for {pid}/{role}")
        seen.add(key)
        path = item.get("source_log_path")
        digest = item.get("source_log_sha256")
        if not isinstance(path, str) or not path:
            issues.append(f"manual protocol source log path is missing for {pid}/{role}")
        elif classify_log_role(path) != role:
            issues.append(f"manual protocol source log filename has the wrong process role for {pid}/{role}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            issues.append(f"manual protocol source log SHA-256 is invalid for {pid}/{role}")
        if not isinstance(item.get("quant_clock"), int):
            issues.append(f"manual protocol quant clock is missing for {pid}/{role}")
        if not isinstance(item.get("local_checkpoint_sequence"), int):
            issues.append(f"manual protocol local checkpoint sequence is missing for {pid}/{role}")
        if not item.get("internal_mission_path"):
            issues.append(f"manual protocol mission source is missing for {pid}/{role}")
        else:
            mission_sources.add(str(item["internal_mission_path"]).casefold())
        if not item.get("virtual_archive_base_path"):
            issues.append(f"manual protocol archive source is missing for {pid}/{role}")
    for player in expected_players:
        if (player, "client") not in seen:
            issues.append(f"manual protocol has no client log source for participant {player}")
    host_id = protocol.get("host_player_id")
    if host_id not in expected_players:
        issues.append("manual protocol host player ID is outside the expected roster")
    elif (host_id, "server") not in seen:
        issues.append("manual protocol has no separate host server log source")
    expected_sources = {(player, "client") for player in expected_players}
    if host_id in expected_players:
        expected_sources.add((host_id, "server"))
    unexpected_sources = seen - expected_sources
    if unexpected_sources:
        issues.append("manual protocol contains process sources outside the client roster plus host server")
    if len(mission_sources) > 1:
        issues.append("manual protocol mission paths differ across participant process logs")
    return not issues, issues


def validate_peer_group(packages: list[str | Path], host_player_id: str,
                        consistency_protocol: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check separate participant ZIPs agree as a file set; this does not prove game synchronization."""
    issues: list[str] = []
    if len(packages) < 2:
        issues.append("at least two participant packages are required")
    records = []
    for package in packages:
        try:
            metadata, package_issues = inspect_peer_package(package)
            issues.extend(f"{Path(package).name}: {issue}" for issue in package_issues)
            records.append(metadata)
        except Exception as exc:
            issues.append(f"{Path(package).name}: {exc}")
    if not records:
        return {"status": "INCOMPLETE", "issues": issues, "participants": []}
    fields = ("checkpoint_id", "mission_id", "game_build", "mod_fingerprint")
    reference = records[0]
    for record in records[1:]:
        for field in fields:
            if record.get(field) != reference.get(field):
                issues.append(f"{field} mismatch across participant packages")
        if set(record.get("expected_players", [])) != set(reference.get("expected_players", [])):
            issues.append("participant roster mismatch across packages")
    ids = [record.get("participant_id") for record in records]
    if len(ids) != len(set(ids)):
        issues.append("duplicate participant package")
    roster = set(reference.get("expected_players", []))
    if set(ids) != roster:
        issues.append("package participant set does not exactly match expected roster")
    hosts = [r for r in records if r.get("participant_id") == host_player_id]
    if len(hosts) != 1:
        issues.append("host participant package is missing or ambiguous")
    elif not hosts[0].get("includes_host_server"):
        issues.append("host package is missing its local server archive")
    for record in records:
        if record.get("participant_id") != host_player_id and record.get("includes_host_server"):
            issues.append(f"server archive is present in non-host package {record.get('participant_id')}")
    protocol_ok = False
    protocol_issues = ["manual checkpoint consistency protocol not supplied"]
    if consistency_protocol is not None and records:
        protocol_ok, protocol_issues = validate_consistency_protocol(
            consistency_protocol, list(reference.get("expected_players", [])), str(reference.get("mission_id")),
            str(reference.get("checkpoint_id")))
    return {"status": "GROUP_FILE_SET_VERIFIED" if not issues else "INCOMPLETE_OR_MISMATCHED",
            "checkpoint_id": reference.get("checkpoint_id"), "mission_id": reference.get("mission_id"),
            "game_build": reference.get("game_build"), "mod_fingerprint": reference.get("mod_fingerprint"),
            "expected_players": reference.get("expected_players"), "participants": ids,
            "host_player_id": host_player_id, "issues": issues,
            "game_restore_status": "NOT_VERIFIED_BY_FILE_SET",
            "checkpoint_consistency_status": "OPERATOR_PROTOCOL_RECORDED_NOT_SYNCHRONIZATION_PROOF" if protocol_ok else "MISSING_OR_INCOMPLETE_MANUAL_PROTOCOL",
            "checkpoint_consistency_issues": protocol_issues,
            "automatic_marker_preparation": "ELIGIBLE_FOR_LOCAL_PREFLIGHT_ONLY" if protocol_ok and not issues else "BLOCKED"}


def import_peer_package(package: str | Path, target_dir: str | Path,
                        expected_checkpoint: str | None = None,
                        expected_mission: str | None = None,
                        expected_build: str | None = None,
                        expected_mods: str | None = None) -> dict[str, Any]:
    meta, issues = inspect_peer_package(package)
    if issues:
        raise HarnessError("Package validation failed: " + "; ".join(issues))
    for key, expected in (("checkpoint_id", expected_checkpoint), ("mission_id", expected_mission),
                          ("game_build", expected_build), ("mod_fingerprint", expected_mods)):
        if expected is not None and meta.get(key) != expected:
            raise HarnessError(f"Package {key} mismatch")
    target = Path(target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []
    try:
        with zipfile.ZipFile(package) as z:
            role_to_target = {"client": target / "coop_ckpt.sav", "server": target / "coop_ckpt_srv.sav"}
            metadata_file = target / "recovery_package.json"
            if metadata_file.exists() and json.loads(metadata_file.read_text(encoding="utf-8")) != meta:
                raise HarnessError("Refusing to overwrite metadata for a different imported package")
            for item in meta["files"]:
                dest = role_to_target[item["role"]]
                if dest.exists():
                    if dest.stat().st_size == item["size_bytes"] and _digest(dest) == item["sha256"]:
                        # Idempotent restart after a process interruption or repeat import.
                        continue
                    raise HarnessError(f"Refusing to overwrite local recovery archive: {dest.name}")
                tmp = dest.with_name(dest.name + ".partial")
                try:
                    tmp.unlink(missing_ok=True)
                    with z.open(item["path"]) as src, tmp.open("xb") as out:
                        shutil.copyfileobj(src, out)
                        out.flush()
                        os.fsync(out.fileno())
                    if _digest(tmp) != item["sha256"]:
                        raise HarnessError(f"Imported archive failed SHA-256 verification: {dest.name}")
                except Exception:
                    tmp.unlink(missing_ok=True)
                    raise
                os.replace(tmp, dest)
                published.append(dest)
        metadata_file = target / "recovery_package.json"
        if not metadata_file.exists():
            _write_json(metadata_file, meta)
            published.append(metadata_file)
        return {"metadata": meta, "files": [str(p) for p in published]}
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class Marker:
    mission: str
    archive: str
    players_base: int | None = None
    players_count: int | None = None


def parse_marker(path: str | Path, now: float | None = None) -> tuple[Marker | None, list[str]]:
    p = Path(path)
    if not p.is_file():
        return None, ["marker not found"]
    age = (time.time() if now is None else now) - p.stat().st_mtime
    if age > MARKER_TTL_SECONDS:
        return None, [f"marker expired ({int(age)} seconds; limit {MARKER_TTL_SECONDS})"]
    raw = p.read_text(encoding="mbcs" if os.name == "nt" else "utf-8", errors="replace")
    lines = raw.splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
        return None, ["marker must contain mission and archive lines"]
    base = count = None
    if len(lines) >= 3:
        import re
        # Match the published sscanf("players %d %d") acceptance rules, including signs
        # and ignored trailing tokens; subsequent range checks match the source.
        m = re.match(r"players\s+([+-]?\d+)\s+([+-]?\d+)", lines[2])
        if m and 0 <= int(m.group(1)) < 18 and int(m.group(2)) > 0:
            base, count = int(m.group(1)), int(m.group(2))
    return Marker(lines[0].strip(), lines[1].strip(), base, count), []


def marker_match(marker: Marker, mission: str, archive_exists: bool = True) -> list[str]:
    issues = []
    if marker.mission.casefold() != mission.casefold():
        issues.append("mission path mismatch")
    if not archive_exists:
        issues.append("archive path does not exist")
    return issues


def prepare_marker(checkpoint_dir: str | Path, package_metadata: dict[str, Any], role: str,
                   local_archive: str | Path, marker_dir: str | Path,
                   ready_to_start_confirmed: bool = False) -> Path:
    """Write one local marker. Called only after operator confirms all peers are ready."""
    if role not in {"client", "server"}:
        raise HarnessError("Role must be client or server")
    if not ready_to_start_confirmed:
        raise HarnessError("All participants must confirm lobby readiness before marker activation")
    checkpoint_path = Path(checkpoint_dir).resolve()
    if (checkpoint_path / "manifest.json").is_file():
        manifest, issues = read_checkpoint(checkpoint_path)
        if issues:
            raise HarnessError("Checkpoint is not intact: " + "; ".join(issues))
    elif (checkpoint_path / "recovery_package.json").is_file():
        manifest = json.loads((checkpoint_path / "recovery_package.json").read_text(encoding="utf-8"))
        if manifest.get("format") != PACKAGE_FORMAT:
            raise HarnessError("Local recovery package metadata format is invalid")
    else:
        raise HarnessError("Checkpoint manifest or imported recovery_package.json is required")
    required_meta = ("internal_mission_path", "archive_path", "player_id", "game_build", "mission_id", "mod_fingerprint")
    absent = [k for k in required_meta if not package_metadata.get(k)]
    if absent:
        raise HarnessError("Missing required recovery metadata: " + ", ".join(absent))
    if any(isinstance(package_metadata.get(k), str) and "REPLACE_WITH_" in package_metadata[k] for k in required_meta):
        raise HarnessError("Recovery metadata still contains template placeholders")
    if package_metadata.get("checkpoint_id") != manifest.get("checkpoint_id") or package_metadata["game_build"] != manifest["game_build"] or package_metadata["mission_id"] != manifest["mission_id"] or package_metadata["mod_fingerprint"] != manifest["mod_fingerprint"]:
        raise HarnessError("Recovery metadata differs from checkpoint version/mission/mod fingerprint")
    player = package_metadata["player_id"]
    if player not in manifest.get("expected_players", []):
        raise HarnessError("Player ID is not in checkpoint roster")
    if manifest.get("participant_id") not in (None, player):
        raise HarnessError("Imported package is assigned to a different participant")
    protocol = package_metadata.get("consistency_protocol")
    if not isinstance(protocol, dict):
        raise HarnessError("Shared checkpoint identity is not confirmed; supply the complete manual consistency protocol")
    protocol_ok, protocol_issues = validate_consistency_protocol(
        protocol, list(manifest.get("expected_players", [])), str(manifest.get("mission_id")),
        str(manifest.get("checkpoint_id")))
    if not protocol_ok:
        raise HarnessError("Manual checkpoint protocol is incomplete: " + "; ".join(protocol_issues))
    archive = Path(local_archive).resolve(strict=True)
    expected_name = coordinator.SERVER_SAVE if role == "server" else coordinator.CLIENT_SAVE
    if archive.name.casefold() != expected_name.casefold():
        raise HarnessError(f"Expected local archive name {expected_name}")
    owner = "host_server" if role == "server" else f"participant:{player}"
    records = manifest.get("files")
    if not isinstance(records, list):
        raise HarnessError("Checkpoint manifest has no file inventory")
    if manifest.get("format") == PACKAGE_FORMAT:
        if manifest.get("participant_id") != player:
            raise HarnessError("Recovery package owner does not match the selected participant")
        if role == "server" and not manifest.get("includes_host_server"):
            raise HarnessError("Recovery package has no declared host server archive")
        role_records = [f for f in records if isinstance(f, dict) and f.get("role") == role]
        expected_rel = "archives/server.sav" if role == "server" else "archives/client.sav"
        if any(f.get("path") != expected_rel for f in role_records):
            raise HarnessError("Recovery package role points at an unexpected archive path")
        expected_owner = "host_server" if role == "server" else f"participant:{player}"
        if role_records and any(f.get("owner") not in (None, expected_owner) for f in role_records):
            raise HarnessError("Recovery package archive owner does not match requested role and participant")
    else:
        role_records = [f for f in records if isinstance(f, dict) and f.get("owner") == owner]
        expected_kind = "host_server" if role == "server" else "participant"
        if role_records and any(f.get("kind") != expected_kind for f in role_records):
            raise HarnessError("Checkpoint archive owner record has the wrong role kind")
    if len(role_records) != 1:
        raise HarnessError(f"Missing or duplicate required archive record for {owner}; marker preparation refused")
    role_record = role_records[0]
    expected_digest = role_record.get("sha256")
    expected_size = role_record.get("size_bytes")
    if (not isinstance(expected_size, int) or expected_size <= 0 or
            not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_digest)):
        raise HarnessError("Required archive manifest record has no valid size/SHA-256")
    if archive.stat().st_size != expected_size or _digest(archive).casefold() != expected_digest.casefold():
        raise HarnessError("Selected local archive size/SHA-256 differs from the required owner record")
    if role == "server" and not package_metadata.get("is_host"):
        raise HarnessError("Server marker can only be prepared on the host")
    mapping = package_metadata.get("player_mapping")
    if not isinstance(mapping, dict) or set(mapping) != {"base", "count"}:
        raise HarnessError("Exact player_mapping {base,count} is required for ownership remapping")
    base, count = mapping["base"], mapping["count"]
    if not isinstance(base, int) or not isinstance(count, int) or not 0 <= base < 18 or count <= 0:
        raise HarnessError("Player mapping is outside documented marker bounds")
    archive_path = package_metadata["archive_path"]
    if not isinstance(archive_path, str) or not archive_path.endswith("/") or archive_path.casefold().endswith(".sav/"):
        raise HarnessError("archive_path must be the exact loader archive base path ending in '/', not a Windows .sav filename")
    profile_root = package_metadata.get("profile_root")
    profile_id = package_metadata.get("profile_id")
    if not profile_root or not profile_id:
        raise HarnessError("Exact local profile_root and profile_id are required to resolve the virtual archive path")
    resolution = ArchivePathResolver().resolve(archive_path, profile_root, str(profile_id), role)
    if resolution.status != "RESOLVED_LOCAL_FILE" or resolution.local_file is None:
        raise HarnessError("UNVERIFIED_PATH: " + resolution.reason)
    if resolution.local_file.stat().st_size != archive.stat().st_size or _digest(resolution.local_file) != _digest(archive):
        raise HarnessError("Resolved profile archive differs from the selected checkpoint archive")
    source_log_path = package_metadata.get("source_log_path")
    source_log_sha256 = package_metadata.get("source_log_sha256")
    if not source_log_path or not source_log_sha256:
        raise HarnessError("Missing role-specific Coop Checkpoint log provenance; process TEMP cannot be established")
    source_log = Path(source_log_path).resolve(strict=True)
    if classify_log_role(source_log) != role:
        raise HarnessError("Role-specific log filename does not match requested marker role")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(source_log_sha256)) or sha256_file(source_log).casefold() != str(source_log_sha256).casefold():
        raise HarnessError("Role-specific log SHA-256 is missing or changed since preflight")
    if package_metadata.get("process_role") != role:
        raise HarnessError("Metadata process role does not match requested marker role")
    protocol_player = package_metadata.get("host_player_id") if role == "server" else player
    protocol_sources = [item for item in protocol.get("participants", []) if isinstance(item, dict)
                        and item.get("participant_id") == protocol_player
                        and item.get("process_role") == role]
    if len(protocol_sources) != 1:
        raise HarnessError("Manual protocol does not contain exactly one source for this participant process role")
    source_record = protocol_sources[0]
    if (Path(source_record.get("source_log_path", "")).resolve() != source_log
            or source_record.get("source_log_sha256", "").casefold() != str(source_log_sha256).casefold()):
        raise HarnessError("Selected role log differs from the source recorded in the manual checkpoint protocol")
    observed_fields = package_metadata.get("observed_log_fields")
    if not isinstance(observed_fields, dict):
        raise HarnessError("Selected role log parsed fields are required for preflight")
    for field_name, protocol_name in (("internal_mission_path", "internal_mission_path"),
                                      ("virtual_archive_base_path", "virtual_archive_base_path"),
                                      ("quant_clock", "quant_clock"),
                                      ("checkpoint_id", "local_checkpoint_sequence")):
        if observed_fields.get(field_name) != source_record.get(protocol_name):
            raise HarnessError(f"Selected role log {field_name} differs from its manual protocol record")
    if role == "server" and package_metadata.get("host_player_id") != player:
        raise HarnessError("Server marker participant must match the recorded host player ID")
    marker_root = Path(marker_dir).resolve()
    if marker_root != source_log.parent.resolve():
        raise HarnessError("Marker directory differs from the TEMP directory observed from this role's exact process log")
    path = marker_root / (SERVER_MARKER if role == "server" else CLIENT_MARKER)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise HarnessError("A marker already exists; inspect and back it up before replacing")
    temp = path.with_name(path.name + ".partial")
    # Coop Checkpoint strips .sav and retains the virtual archive base (normally ending in '/').
    # The absolute local .sav path is only used for validation; it is not the marker value.
    body = f"{package_metadata['internal_mission_path']}\n{archive_path}\nplayers {base} {count}\n"
    with temp.open("x", encoding="mbcs" if os.name == "nt" else "utf-8", newline="\n") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    return path


def backup_marker(marker_path: str | Path, backup_dir: str | Path) -> Path:
    source = Path(marker_path)
    if not source.exists():
        raise HarnessError("Marker does not exist")
    target = Path(backup_dir).resolve() / f"{source.name}.{int(time.time())}.backup"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def read_coop_diagnostics(temp_dir: str | Path, max_lines: int = 100,
                          role_logs: dict[str, str | Path] | None = None) -> list[str]:
    """Read exact process-role logs separately; never choose a cross-role latest file."""
    root = Path(temp_dir)
    user_root = str(Path.home())
    safe = []
    logs = (("client process", Path(role_logs["client"]) if role_logs and role_logs.get("client") else root / "goh_coop_checkpoint_call_to_arms.exe.log"),
            ("server process", Path(role_logs["server"]) if role_logs and role_logs.get("server") else root / "goh_coop_checkpoint_call_to_arms_server.exe.log"))
    for label, path in logs:
        safe.append(f"[{label}] source={path}")
        if not path.is_file():
            safe.append("exact role log not found in selected directory")
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            safe.append(f"Could not read log: {exc}")
            continue
        selected = [line for line in lines if re.search(r"error|warning|reload|checkpoint|marker", line, re.I)][-max_lines:]
        if not selected:
            safe.append("No checkpoint/reload/marker/error lines")
            continue
        for line in selected:
            line = re.sub(re.escape(user_root), "%USERPROFILE%", line, flags=re.I)
            line = re.sub(r"(?i)[A-Z]:\\Users\\[^\\/]+", "%USERPROFILE%", line)
            line = re.sub(r"(?i)(/profiles/)[^/\\]+", r"\1<redacted>", line)
            line = re.sub(r"(?i)(\\profiles\\)[^\\/]+", r"\1<redacted>", line)
            safe.append(line[:1000])
    return safe
