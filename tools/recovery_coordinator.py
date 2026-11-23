#!/usr/bin/env python3
"""Filesystem-only GOH coop checkpoint inspector and recovery coordinator.

This tool inventories and copies checkpoint archives. It does not parse the game's
proprietary save format, control the game, or claim that a file is loadable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STATUSES = {"INCOMPLETE", "FILE_VERIFIED", "RESTORE_TEST_PENDING", "RESTORE_VERIFIED", "RESTORE_FAILED"}
CLIENT_SAVE = "coop_ckpt.sav"
SERVER_SAVE = "coop_ckpt_srv.sav"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class CoordinatorError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoordinatorError(f"Invalid checkpoint timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise CoordinatorError("Checkpoint timestamp must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value) or value.upper().split(".", 1)[0] in WINDOWS_RESERVED:
        raise CoordinatorError(f"Invalid {label}: {value!r}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_fsynced(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def lock_exclusive(stream) -> None:
    """Acquire an OS-released nonblocking lock; stale lock files are reusable."""
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        raise CoordinatorError("Another request is publishing this checkpoint (lock is held)") from exc


def unlock_exclusive(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_publish_directory(store: Path, identifier: str, populate) -> Path:
    """Populate a same-volume staging directory, then atomically publish it."""
    final = store / identifier
    lock = store / f".{identifier}.lock"
    lock_stream = lock.open("a+b")
    stage: Path | None = None
    locked = False
    try:
        lock_exclusive(lock_stream)
        locked = True
        if final.exists():
            raise CoordinatorError(f"Checkpoint already exists; refusing to overwrite: {identifier}")
        stage = Path(tempfile.mkdtemp(prefix=f".{identifier}.incomplete-", dir=store))
        populate(stage)
        if final.exists():
            raise CoordinatorError(f"Checkpoint appeared during publication; refusing to overwrite: {identifier}")
        os.rename(stage, final)
        return final
    finally:
        # Keep a failed staging directory for diagnostics; published checkpoints are untouched.
        try:
            if locked:
                unlock_exclusive(lock_stream)
        finally:
            lock_stream.close()


def parse_assignment(value: str, what: str) -> tuple[str, Path]:
    if "=" not in value:
        raise CoordinatorError(f"{what} must use NAME=PATH: {value!r}")
    name, raw_path = value.split("=", 1)
    name = safe_id(name, what + " name")
    if not raw_path:
        raise CoordinatorError(f"Missing path in {what}: {value!r}")
    return name, Path(raw_path).expanduser().resolve()


def scan_saves(root: Path | None) -> tuple[list[Path], str | None]:
    if root is None:
        return [], "source directory was not supplied"
    if not root.exists():
        return [], "source directory does not exist"
    if not root.is_dir():
        return [], "source path is not a directory"
    try:
        files = sorted((p for p in root.rglob("*.sav") if p.is_file()), key=lambda p: str(p).lower())
    except OSError as exc:
        return [], f"source scan failed: {exc}"
    return files, None


def relative_source_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def capture(args: argparse.Namespace) -> dict[str, Any]:
    store = Path(args.store).expanduser().resolve()
    store.mkdir(parents=True, exist_ok=True)
    checkpoint_id = safe_id(args.checkpoint_id, "checkpoint ID")
    mission_id = args.mission_id.strip()
    game_build = args.game_build.strip()
    mod_fingerprint = args.mod_fingerprint.strip()
    if not mission_id or not game_build or not mod_fingerprint:
        raise CoordinatorError("mission-id, game-build, and mod-fingerprint must be non-empty")

    expected = [safe_id(x, "player ID") for x in args.expected_player]
    if not expected or len(set(expected)) != len(expected):
        raise CoordinatorError("Expected player IDs must be supplied once each")
    if len(expected) < 2:
        raise CoordinatorError("At least two expected player IDs are required for coop recovery")
    peers: dict[str, Path] = {}
    for item in args.peer:
        player_id, root = parse_assignment(item, "peer")
        if player_id in peers:
            raise CoordinatorError(f"Duplicate peer source: {player_id}")
        peers[player_id] = root
    extra = sorted(set(peers) - set(expected))
    if extra:
        raise CoordinatorError(f"Peer sources not in expected roster: {', '.join(extra)}")
    server_root = Path(args.server_root).expanduser().resolve() if args.server_root else None

    source_roots = list(peers.values()) + ([server_root] if server_root else [])
    for root in source_roots:
        if root == store or root in store.parents or store in root.parents:
            raise CoordinatorError("Store and source directories must not overlap")

    inventory: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    missing: list[str] = []
    copy_jobs: list[tuple[str, Path, Path, str]] = []

    for player_id in expected:
        root = peers.get(player_id)
        found, issue = scan_saves(root)
        if issue:
            diagnostics.append(f"participant {player_id}: {issue}")
        canonical = [path for path in found if path.name.casefold() == CLIENT_SAVE.casefold()]
        canonical_found = bool(canonical)
        if len(canonical) > 1:
            missing.append(f"participant:{player_id}/ambiguous-{CLIENT_SAVE}")
            diagnostics.append(f"participant {player_id}: found {len(canonical)} matching archives; expected exactly one")
        for source_file in canonical:
            rel = relative_source_path(source_file, root)  # type: ignore[arg-type]
            target_rel = Path("data") / "participants" / player_id / Path(rel)
            copy_jobs.append(("participant", source_file, target_rel, f"participant:{player_id}"))
        if not canonical_found:
            missing.append(f"participant:{player_id}/{CLIENT_SAVE}")
            diagnostics.append(f"participant {player_id}: required {CLIENT_SAVE} not found")
        inventory.append({"kind": "participant", "id": player_id, "source_supplied": root is not None})

    server_files, server_issue = scan_saves(server_root)
    if server_issue:
        diagnostics.append(f"host_server: {server_issue}")
    canonical_server = [path for path in server_files if path.name.casefold() == SERVER_SAVE.casefold()]
    server_found = bool(canonical_server)
    if len(canonical_server) > 1:
        missing.append(f"host_server/ambiguous-{SERVER_SAVE}")
        diagnostics.append(f"host_server: found {len(canonical_server)} matching archives; expected exactly one")
    for source_file in canonical_server:
        rel = relative_source_path(source_file, server_root)  # type: ignore[arg-type]
        target_rel = Path("data") / "host_server" / Path(rel)
        copy_jobs.append(("host_server", source_file, target_rel, "host_server"))
    if not server_found:
        missing.append(f"host_server/{SERVER_SAVE}")
        diagnostics.append(f"host_server: required {SERVER_SAVE} not found")
    inventory.append({"kind": "host_server", "id": "host_server", "source_supplied": server_root is not None})

    captured_at = utc_now()
    checkpoint_time = normalize_utc(args.checkpoint_time_utc)
    status = "INCOMPLETE" if missing else "FILE_VERIFIED"
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "captured_at_utc": captured_at,
        "checkpoint_time_utc": checkpoint_time,
        "mission_id": mission_id,
        "game_build": game_build,
        "mod_fingerprint": mod_fingerprint,
        "expected_players": expected,
        "status": status,
        "files": [],
        "sources": inventory,
        "missing": missing,
        "diagnostics": diagnostics,
        "integrity_scope": "SHA-256 and size of copied files only; game save semantics/loadability are not verified",
    }

    def populate(stage: Path) -> None:
        for kind, source_file, target_rel, owner in copy_jobs:
            target = stage / target_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)
            with target.open("r+b") as copied:
                copied.flush()
                os.fsync(copied.fileno())
            size = target.stat().st_size
            item = {
                "owner": owner,
                "kind": kind,
                "path": target_rel.as_posix(),
                "size_bytes": size,
                "sha256": sha256_file(target),
                "valid": size > 0,
            }
            manifest["files"].append(item)
            if size == 0:
                manifest["status"] = "INCOMPLETE"
                manifest["diagnostics"].append(f"empty file: {item['path']}")
                if item["path"] not in manifest["missing"]:
                    manifest["missing"].append(item["path"])
        write_json_fsynced(stage / "manifest.json", manifest)

    published = atomic_publish_directory(store, checkpoint_id, populate)
    return {"manifest": str(published / "manifest.json"), **manifest}


def load_manifest(directory: Path) -> dict[str, Any]:
    manifest_path = directory / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise CoordinatorError(f"Unreadable manifest {manifest_path}: {exc}") from exc
    if value.get("schema_version") != SCHEMA_VERSION:
        raise CoordinatorError(f"Unsupported manifest schema in {manifest_path}")
    if value.get("checkpoint_id") != directory.name:
        raise CoordinatorError(f"Checkpoint directory/manifest ID mismatch: {directory}")
    if value.get("status") not in STATUSES:
        raise CoordinatorError(f"Unknown checkpoint status in {manifest_path}")
    if not isinstance(value.get("expected_players"), list) or not all(isinstance(x, str) for x in value["expected_players"]):
        raise CoordinatorError(f"Invalid expected_players in {manifest_path}")
    if not isinstance(value.get("files"), list) or not isinstance(value.get("sources"), list):
        raise CoordinatorError(f"Invalid files/sources in {manifest_path}")
    if not all(isinstance(x, dict) for x in value["files"] + value["sources"]):
        raise CoordinatorError(f"Invalid file/source record in {manifest_path}")
    return value


def verify_manifest_files(directory: Path, manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return ["manifest files field is not a list"]
    for item in entries:
        if not isinstance(item, dict):
            issues.append("malformed file record in manifest")
            continue
        rel = Path(str(item.get("path", "")))
        if rel.is_absolute() or ".." in rel.parts:
            issues.append(f"unsafe archive path in manifest: {rel}")
            continue
        path = directory / rel
        try:
            resolved = path.resolve(strict=True)
            if directory.resolve() not in resolved.parents:
                issues.append(f"archive escapes checkpoint directory: {rel}")
                continue
            size = resolved.stat().st_size
            digest = sha256_file(resolved)
        except OSError as exc:
            issues.append(f"missing/unreadable file {rel}: {exc}")
            continue
        if size <= 0:
            issues.append(f"empty file: {rel}")
        if size != item.get("size_bytes"):
            issues.append(f"size mismatch: {rel} (manifest {item.get('size_bytes')}, actual {size})")
        if digest != item.get("sha256"):
            issues.append(f"SHA-256 mismatch: {rel}")
    return issues


def compatibility_issues(manifest: dict[str, Any], expected: list[str], mission: str, build: str, mods: str) -> list[str]:
    issues = []
    if set(manifest.get("expected_players", [])) != set(expected):
        issues.append("expected participant roster differs")
    if manifest.get("mission_id") != mission:
        issues.append("mission ID differs")
    if manifest.get("game_build") != build:
        issues.append("game build differs")
    if manifest.get("mod_fingerprint") != mods:
        issues.append("mod fingerprint differs")
    actual_participants = {x.get("id") for x in manifest.get("sources", []) if x.get("kind") == "participant"}
    if actual_participants != set(expected):
        issues.append("participant source set differs")
    if not any(x.get("kind") == "host_server" for x in manifest.get("sources", [])):
        issues.append("host server source is absent")
    return issues


def plan_recovery(args: argparse.Namespace) -> dict[str, Any]:
    store = Path(args.store).expanduser().resolve()
    plans_dir = store / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    request_id = safe_id(args.request_id, "request ID")
    expected = [safe_id(x, "player ID") for x in args.expected_player]
    if len(expected) < 2 or len(set(expected)) != len(expected):
        raise CoordinatorError("Expected player IDs must contain at least two unique participants")
    request = {
        "request_id": request_id,
        "expected_players": expected,
        "mission_id": args.mission_id,
        "game_build": args.game_build,
        "mod_fingerprint": args.mod_fingerprint,
    }
    request_signature = hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()
    plan_path = plans_dir / f"{request_id}.json"
    if plan_path.exists():
        try:
            existing = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CoordinatorError(f"Existing plan request is unreadable: {exc}") from exc
        if existing.get("request_signature") != request_signature:
            raise CoordinatorError("Duplicate request ID has different inputs; refusing to replace the prior plan")
        return existing

    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    excluded: list[dict[str, Any]] = []
    for directory in store.iterdir():
        if not directory.is_dir() or directory.name.startswith(".") or directory.name in {"plans", "results"}:
            continue
        try:
            manifest = load_manifest(directory)
        except CoordinatorError as exc:
            excluded.append({"checkpoint_id": directory.name, "reasons": [str(exc)]})
            continue
        reasons = compatibility_issues(manifest, expected, args.mission_id, args.game_build, args.mod_fingerprint)
        if manifest.get("status") != "FILE_VERIFIED":
            reasons.append(f"file status is {manifest.get('status')}, expected FILE_VERIFIED")
        reasons.extend(verify_manifest_files(directory, manifest))
        if reasons:
            excluded.append({"checkpoint_id": manifest.get("checkpoint_id"), "reasons": reasons})
            continue
        try:
            checkpoint_time = normalize_utc(manifest.get("checkpoint_time_utc", ""))
        except CoordinatorError as exc:
            excluded.append({"checkpoint_id": manifest.get("checkpoint_id"), "reasons": [str(exc)]})
            continue
        candidates.append((checkpoint_time, directory, manifest))

    if not candidates:
        plan = {
            "schema_version": SCHEMA_VERSION,
            **request,
            "request_signature": request_signature,
            "created_at_utc": utc_now(),
            "status": "INCOMPLETE",
            "checkpoint_id": None,
            "archives": [],
            "excluded_candidates": excluded,
            "diagnostics": ["No complete compatible checkpoint is available; no game action was taken."],
        }
    else:
        _, directory, manifest = max(candidates, key=lambda x: (x[0], x[1].name))
        plan = {
            "schema_version": SCHEMA_VERSION,
            **request,
            "request_signature": request_signature,
            "created_at_utc": utc_now(),
            "status": "RESTORE_TEST_PENDING",
            "checkpoint_id": manifest["checkpoint_id"],
            "archives": [
                {"owner": item["owner"], "path": str((directory / item["path"]).resolve()),
                 "size_bytes": item["size_bytes"], "sha256": item["sha256"]}
                for item in manifest["files"]
            ],
            "excluded_candidates": excluded,
            "diagnostics": [
                "All recorded files passed size/SHA-256 checks and the roster/build/mission/mod keys match.",
                "This plan does not prove that any archive is loadable or sufficient for all participants.",
                "Manual real-game restore testing is required; no game action was taken.",
            ],
        }
    try:
        write_json_fsynced(plan_path, plan)
    except FileExistsError:
        # A concurrent duplicate may have completed; accept only an identical request.
        existing = json.loads(plan_path.read_text(encoding="utf-8"))
        if existing.get("request_signature") != request_signature:
            raise CoordinatorError("Concurrent duplicate request ID has different inputs")
        return existing
    return plan


def record_result(args: argparse.Namespace) -> dict[str, Any]:
    if args.status not in {"RESTORE_VERIFIED", "RESTORE_FAILED"}:
        raise CoordinatorError("Only RESTORE_VERIFIED or RESTORE_FAILED can be recorded")
    if not args.confirm_real_game:
        raise CoordinatorError("Explicit --confirm-real-game is required to attest the protocol was run in-game")
    store = Path(args.store).expanduser().resolve()
    checkpoint_id = safe_id(args.checkpoint_id, "checkpoint ID")
    directory = store / checkpoint_id
    manifest = load_manifest(directory)
    issues = verify_manifest_files(directory, manifest)
    if issues:
        raise CoordinatorError("Cannot attach a game result to a damaged checkpoint: " + "; ".join(issues))
    protocol_path = Path(args.protocol).expanduser().resolve()
    try:
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoordinatorError(f"Could not read experiment protocol JSON: {exc}") from exc
    required = ["test_id", "executed_at_utc", "game_build", "participant_ids", "outcome", "evidence_files"]
    absent = [field for field in required if field not in protocol]
    if absent:
        raise CoordinatorError("Experiment protocol missing fields: " + ", ".join(absent))
    if protocol.get("checkpoint_id") != checkpoint_id:
        raise CoordinatorError("Experiment protocol checkpoint_id does not match")
    if protocol.get("experiment_type") != "real_game":
        raise CoordinatorError("Only experiment_type=real_game is accepted for restore results")
    participants = protocol.get("participant_ids")
    if not isinstance(participants, list) or not all(isinstance(x, str) for x in participants) or len(set(participants)) < 2:
        raise CoordinatorError("Protocol must identify at least two real participants")
    if set(participants) != set(manifest.get("expected_players", [])):
        raise CoordinatorError("Protocol participant roster does not match the checkpoint roster")
    if protocol.get("game_build") != manifest.get("game_build"):
        raise CoordinatorError("Protocol game_build does not match the checkpoint")
    if not isinstance(protocol.get("evidence_files"), list) or not protocol["evidence_files"]:
        raise CoordinatorError("Protocol must reference saved evidence files")
    for evidence in protocol["evidence_files"]:
        if not isinstance(evidence, str) or not evidence:
            raise CoordinatorError("Protocol evidence_files entries must be non-empty paths")
        evidence_path = Path(evidence)
        if not evidence_path.is_absolute():
            evidence_path = protocol_path.parent / evidence_path
        if not evidence_path.is_file():
            raise CoordinatorError(f"Protocol evidence file does not exist: {evidence_path}")
    expected_outcome = "PASS" if args.status == "RESTORE_VERIFIED" else "FAIL"
    if protocol.get("outcome") != expected_outcome:
        raise CoordinatorError(f"Protocol outcome must be {expected_outcome} for {args.status}")
    result = {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "status": args.status,
        "recorded_at_utc": utc_now(),
        "protocol": protocol,
    }
    results_dir = store / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"{checkpoint_id}.json"
    try:
        write_json_fsynced(result_path, result)
    except FileExistsError:
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        if existing.get("protocol") != protocol or existing.get("status") != args.status:
            raise CoordinatorError("A different result already exists for this checkpoint; refusing to replace it")
        return existing
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and coordinate GOH coop checkpoint files; no game integration.")
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="inventory and atomically publish an immutable checkpoint package")
    cap.add_argument("--store", required=True)
    cap.add_argument("--checkpoint-id", required=True)
    cap.add_argument("--checkpoint-time-utc", required=True, help="time the game checkpoint was saved, with timezone (e.g. 2026-11-23T14:30:00Z)")
    cap.add_argument("--mission-id", required=True)
    cap.add_argument("--game-build", required=True)
    cap.add_argument("--mod-fingerprint", required=True)
    cap.add_argument("--expected-player", action="append", default=[])
    cap.add_argument("--peer", action="append", default=[], metavar="PLAYER=DIR")
    cap.add_argument("--server-root", help="directory containing host local-server archive")
    cap.set_defaults(func=capture)

    plan = sub.add_parser("plan", help="choose the latest complete compatible checkpoint; does not load it")
    plan.add_argument("--store", required=True)
    plan.add_argument("--request-id", required=True)
    plan.add_argument("--mission-id", required=True)
    plan.add_argument("--game-build", required=True)
    plan.add_argument("--mod-fingerprint", required=True)
    plan.add_argument("--expected-player", action="append", default=[])
    plan.set_defaults(func=plan_recovery)

    result = sub.add_parser("record-result", help="attach a real two-player game protocol to a checkpoint result")
    result.add_argument("--store", required=True)
    result.add_argument("--checkpoint-id", required=True)
    result.add_argument("--status", required=True, choices=["RESTORE_VERIFIED", "RESTORE_FAILED"])
    result.add_argument("--protocol", required=True, help="JSON protocol from a real game experiment")
    result.add_argument("--confirm-real-game", action="store_true", help="attest that the protocol describes an actual game run")
    result.set_defaults(func=record_result)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except (CoordinatorError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
