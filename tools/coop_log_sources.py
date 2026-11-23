#!/usr/bin/env python3
"""Exact source filenames declared by pinned Coop Checkpoint/common source."""
from __future__ import annotations
import hashlib
from pathlib import Path

CLIENT_LOG_NAME = "goh_coop_checkpoint_call_to_arms.exe.log"
SERVER_LOG_NAME = "goh_coop_checkpoint_call_to_arms_server.exe.log"


class LogSourceError(ValueError):
    pass


def classify_log_role(path: str | Path) -> str | None:
    name = Path(path).name.casefold()
    if name == CLIENT_LOG_NAME.casefold():
        return "client"
    if name == SERVER_LOG_NAME.casefold():
        return "server"
    return None


def select_role_log(paths: list[str | Path], role: str) -> Path:
    if role not in {"client", "server"}:
        raise LogSourceError(f"Unsupported process role: {role}")
    candidates = []
    for path in paths:
        p = Path(path).expanduser().resolve()
        if p.is_file() and classify_log_role(p) == role:
            candidates.append(p)
    # Distinct folders/files remain ambiguous even when the filenames are authoritative.
    unique = sorted(set(candidates), key=lambda p: str(p).casefold())
    if len(unique) == 0:
        raise LogSourceError(f"MISSING_ROLE_LOG: no exact {role} process log")
    if len(unique) != 1:
        raise LogSourceError(f"AMBIGUOUS_ROLE_LOG: found {len(unique)} exact {role} process logs")
    return unique[0]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
