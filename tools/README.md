# Recovery Coordinator and automated tests

The Python 3.10+ prototype operates on files only. It does not load proprietary archives, inject into the game, inspect network packets, or call game APIs. It reads P0.5 schema v1 and writes manifest schema v2.

## Capture immutable archives

`tools/find_goh_installation.ps1` discovers Steam roots and extra libraries from `libraryfolders.vdf`; for a portable Steam root outside registered paths, pass `-SteamRoot`. Each peer source directory should contain its `coop_ckpt.sav`; the host server source should contain `coop_ckpt_srv.sav`. The host may appear both as a peer source and server source. Use a new ID for every capture.

```powershell
python tools/recovery_coordinator.py capture `
  --store E:\GOH-evidence\checkpoint-store `
  --checkpoint-id mission1-20261123-143000 `
  --capture-run-id capture-001 `
  --checkpoint-time-utc 2026-11-23T14:30:00Z `
  --mission-id historical-mission-id `
  --game-build 1.067.0 `
  --mod-fingerprint mods-sha256-here `
  --expected-player host --expected-player guest `
  --peer host=E:\GOH-evidence\host-profile\save `
  --peer guest=E:\GOH-evidence\guest-profile\save `
  --server-root E:\GOH-evidence\host-server-profile\save
```

Only the recognized `coop_ckpt.sav` file for each peer and `coop_ckpt_srv.sav` for the host server are copied; unrelated profile saves are ignored. If more than one matching archive is found for a role, the manifest is `INCOMPLETE` / `SOURCE_AMBIGUOUS`; missing canonical files produce `INCOMPLETE`. Each source is statted/hashed before and after copying and the copied hash is compared with both. A changed source gets `SOURCE_UNSTABLE` and is never plan-eligible. A copy/read exception writes a separate `CAPTURE_ERROR` diagnostic; a failed copy may also leave `.incomplete-*` staging data. This detects mutation during copy but does not prove the game engine finished writing before capture. `checkpoint-time-utc` is operator supplied, not read from the archive. Publication uses same-volume atomic rename; reusing checkpoint ID is rejected.

## Plan recovery

```powershell
python tools/recovery_coordinator.py plan `
  --store E:\GOH-evidence\checkpoint-store `
  --request-id new-lobby-attempt-01 `
  --mission-id historical-mission-id `
  --game-build 1.067.0 `
  --mod-fingerprint mods-sha256-here `
  --expected-player host --expected-player guest
```

The plan selects the latest physically available compatible checkpoint with the exact participant roster, mission, build, mod fingerprint, client/server archives, and valid hashes. It reports both the latest physically available checkpoint and the last successful game test (including TEST-B) separately. Every plan expires after 15 minutes; changed files/history, expiry, or a newer checkpoint makes its request return `STALE_PLAN`. A fresh request must use a new request ID; existing plans are never rewritten.

## Record a real game result

Create a protocol JSON for an actual game experiment with `experiment_type: "real_game"`, `checkpoint_id`, unique `test_run_id`, `test_id` (`TEST-A`…`TEST-F`), `executed_at_utc`, `game_build`, exactly the checkpoint's `participant_ids`, `outcome` (`PASS` or `FAIL`) and nonempty existing `evidence_files`. Then call the matching status:

```powershell
python tools/recovery_coordinator.py record-result `
  --store E:\GOH-evidence\checkpoint-store `
  --checkpoint-id mission1-20261123-143000 `
  --test-run-id test-b-run-001 --test-id TEST-B `
  --status RESTORE_VERIFIED --protocol E:\GOH-evidence\test-b.json `
  --confirm-real-game
```

Each run is stored separately under `results/runs/<test_run_id>.json`; failed attempts remain alongside retries. The flag is an operator attestation; the tool does not authenticate evidence contents or independently prove that a game run occurred. Unit tests exercise this gate only in a temporary directory.

## Tests

Run the unit tests with:

```powershell
python -m unittest discover -s tools/tests -v
```

Tests create synthetic archives in temporary directories and never use them as evidence of game recovery.
