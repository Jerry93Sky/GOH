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
    archive = Path(local_archive).resolve(strict=True)
    expected_name = coordinator.SERVER_SAVE if role == "server" else coordinator.CLIENT_SAVE
    if archive.name.casefold() != expected_name.casefold():
        raise HarnessError(f"Expected local archive name {expected_name}")
    owner = "host_server" if role == "server" else f"participant:{player}"
    role_record = next((f for f in manifest.get("files", []) if f.get("role") == role or f.get("owner") == owner), None)
    if role_record:
        if archive.stat().st_size != role_record.get("size_bytes") or _digest(archive) != role_record.get("sha256"):
            raise HarnessError("Selected local archive does not match the checkpoint package")
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
    path = Path(marker_dir).resolve() / (SERVER_MARKER if role == "server" else CLIENT_MARKER)
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


def read_coop_diagnostics(temp_dir: str | Path, max_lines: int = 100) -> list[str]:
    """Read relevant Coop Checkpoint log lines and redact common profile path prefixes."""
    root = Path(temp_dir)
    logs = sorted(root.glob("goh_coop_checkpoint_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return ["Coop Checkpoint log not found in TEMP"]
    try:
        lines = logs[0].read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"Could not read Coop Checkpoint log: {exc}"]
    selected = [line for line in lines if re.search(r"error|warning|reload|checkpoint|marker", line, re.I)][-max_lines:]
    if not selected:
        return ["No checkpoint/reload/marker/error lines in latest log"]
    user_root = str(Path.home())
    safe = []
    for line in selected:
        line = re.sub(re.escape(user_root), "%USERPROFILE%", line, flags=re.I)
        line = re.sub(r"(?i)[A-Z]:\\Users\\[^\\/]+", "%USERPROFILE%", line)
        line = re.sub(r"(?i)(/profiles/)[^/\\]+", r"\1<redacted>", line)
        line = re.sub(r"(?i)(\\profiles\\)[^/\\]+", r"\1<redacted>", line)
        safe.append(line[:1000])
    return safe
