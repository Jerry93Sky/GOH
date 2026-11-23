#!/usr/bin/env python3
"""Append-only operator field-test notes, explicitly separate from verified restore records."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

import recovery_coordinator as coordinator


def record_field_outcome(store: str | Path, checkpoint_id: str, test_id: str, outcome: str,
                         evidence_files: list[str | Path] | None = None,
                         confirmed_real_game: bool = False, notes: str = "") -> Path:
    if test_id not in {"TEST-A", "TEST-B"}:
        raise ValueError("Only TEST-A and TEST-B are supported by this field-test form")
    if outcome not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        raise ValueError("Outcome must be PASS, FAIL or INCONCLUSIVE")
    coordinator.safe_id(checkpoint_id, "checkpoint ID")
    evidence = [Path(p).resolve(strict=True) for p in (evidence_files or [])]
    if any(not p.is_file() for p in evidence):
        raise ValueError("Evidence entries must be existing files")
    if outcome in {"PASS", "FAIL"} and (not confirmed_real_game or not evidence):
        raise ValueError("PASS/FAIL requires explicit real-game confirmation and at least one evidence file")
    # Verify the target checkpoint exists, without mutating its manifest or contents.
    root = Path(store).expanduser().resolve()
    manifest_path = root / checkpoint_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("checkpoint_id") != checkpoint_id:
        raise ValueError("Selected checkpoint manifest ID mismatch")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    report = {
        "schema": "goh-field-test-operator-report-v1",
        "report_id": run_id,
        "checkpoint_id": checkpoint_id,
        "test_id": test_id,
        "operator_reported_outcome": outcome,
        "confirmed_real_game": bool(confirmed_real_game),
        "evidence_file_names": [p.name for p in evidence],
        "notes": notes[:2000],
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "restore_verified_status": "NOT_SET_BY_THIS_REPORT",
    }
    destination = root / "results" / "field-test-reports" / f"{run_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    return destination
