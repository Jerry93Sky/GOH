#!/usr/bin/env python3
"""Conservative resolver for the published Coop Checkpoint archive naming contract."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


CONTRACT = "Coop Checkpoint 1.0.4 published source: virtual @/profiles/<id>/save/coop_ckpt[_srv]/"


@dataclass(frozen=True)
class Resolution:
    status: str
    virtual_path: str
    local_file: Path | None
    reason: str
    evidence: str = CONTRACT


class ArchivePathResolver:
    """Resolve only exact documented profile-save archive paths; no heuristic search."""

    _PATTERN = re.compile(r"^@/profiles/([^/]+)/save/(coop_ckpt|coop_ckpt_srv)/$")

    def resolve(self, virtual_path: str, profile_root: str | Path,
                expected_profile_id: str, role: str) -> Resolution:
        if role not in {"client", "server"}:
            return Resolution("UNVERIFIED_PATH", virtual_path, None, "unknown archive role")
        match = self._PATTERN.fullmatch(virtual_path)
        if not match:
            return Resolution("UNVERIFIED_PATH", virtual_path, None,
                              "path does not match the published Coop Checkpoint profile-save contract")
        profile_id, stem = match.groups()
        if profile_id.casefold() != expected_profile_id.casefold():
            return Resolution("UNVERIFIED_PATH", virtual_path, None,
                              "virtual profile id differs from the selected local profile")
        expected_stem = "coop_ckpt" if role == "client" else "coop_ckpt_srv"
        if stem != expected_stem:
            return Resolution("UNVERIFIED_PATH", virtual_path, None,
                              f"virtual archive stem does not match {role} role")
        root = Path(profile_root).expanduser().resolve()
        candidate = (root / "save" / f"{stem}.sav").resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return Resolution("UNVERIFIED_PATH", virtual_path, None, "resolved file escaped selected profile")
        if not candidate.is_file():
            return Resolution("UNVERIFIED_PATH", virtual_path, None,
                              "contract-derived local file is absent; no fallback search is attempted")
        return Resolution("RESOLVED_LOCAL_FILE", virtual_path, candidate,
                          "exact documented path, selected profile id, role and local file all match")
