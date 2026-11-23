# Recovery Coordinator and automated tests

The Python 3.10+ prototype operates on files only. It does not load proprietary archives, inject into the game, inspect network packets, or call game APIs.

## Capture immutable archives

`tools/find_goh_installation.ps1` discovers Steam roots and extra libraries from `libraryfolders.vdf`; for a portable Steam root outside registered paths, pass `-SteamRoot`. Each peer source directory should contain its `coop_ckpt.sav`; the host server source should contain `coop_ckpt_srv.sav`. The host may appear both as a peer source and server source. Use a new ID for every capture.

```powershell
python tools/recovery_coordinator.py capture `
  --store E:\GOH-evidence\checkpoint-store `
  --checkpoint-id mission1-20261123-143000 `
  --checkpoint-time-utc 2026-11-23T14:30:00Z `
  --mission-id historical-mission-id `
  --game-build 1.067.0 `
  --mod-fingerprint mods-sha256-here `
  --expected-player host --expected-player guest `
  --peer host=E:\GOH-evidence\host-profile\save `
  --peer guest=E:\GOH-evidence\guest-profile\save `
  --server-root E:\GOH-evidence\host-server-profile\save
```

Only the recognized `coop_ckpt.sav` file for each peer and `coop_ckpt_srv.sav` for the host server are copied; unrelated profile saves are ignored. If more than one matching archive is found for a role, the manifest is `INCOMPLETE` and records the ambiguity. Missing canonical files also produce an `INCOMPLETE` manifest. `checkpoint-time-utc` is entered by the operator and used to select the latest game checkpoint; it is not read from or verified against archive contents. A completed directory is published by same-volume atomic rename only after its files and manifest are flushed. A failed copy may leave a `.incomplete-*` staging directory for diagnosis; it is never considered a checkpoint. The small `.checkpoint-id.lock` file remains as an OS-released lock target, so an interrupted process does not permanently block later captures. Reusing an existing checkpoint ID is rejected.

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

The plan selects the latest capture with the exact participant roster, mission, build, mod fingerprint, participant snapshots, server snapshot, and intact hashes. It reports `RESTORE_TEST_PENDING`; it contains archive paths for a human experiment and does not launch the game. Repeating the same request ID and inputs returns the same plan. Reusing that request ID with different inputs fails.

## Record a real game result

Create a protocol JSON for an actual game experiment with `experiment_type: "real_game"`, `checkpoint_id`, `test_id`, `executed_at_utc`, `game_build`, exactly the checkpoint's `participant_ids`, `outcome` (`PASS` or `FAIL`) and nonempty `evidence_files` that exist on disk. Then call `record-result` with the matching status and `--confirm-real-game`. This adds an immutable result record. The flag is an operator attestation; the tool does not authenticate video contents or independently prove that a game run occurred. Unit tests exercise this gate only in a temporary directory.

## Tests

Run the unit tests with:

```powershell
python -m unittest discover -s tools/tests -v
```

Tests create synthetic archives in temporary directories and never use them as evidence of game recovery.
