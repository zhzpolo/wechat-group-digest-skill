# Operations

## Supported shape

The implementation targets 64-bit Windows desktop WeChat 4.x data under `xwechat_files`. The published validation covers Windows 11 and WeChat 4.1.15.12 with DLL SHA-256 `3a65d5f38991e32129afa83bc1c92b29babf316199b67c7a47614a9100fb03ca`. That hash is a compatibility identity, not a secret or a promise that every 4.1.15.12 build is identical.

Python 3.13 and installed Microsoft Edge are the tested environment. `doctor` discovers processes, account roots, packages, and Edge; it does not read chat text and does not prove compatibility.

## Commands

From the skill directory in PowerShell:

```powershell
.\scripts\setup.ps1
.\scripts\run.ps1 doctor
.\scripts\run.ps1 demo
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

Generate an anchor for the exact installed DLL. Store it locally; the gitignore excludes it:

```powershell
$env:PYTHONPATH = (Resolve-Path .\scripts)
.\.venv\Scripts\python.exe -X utf8 -m wechat_digest.anchors `
  "C:\Program Files\Tencent\Weixin\[version]\Weixin.dll" `
  --output .\anchors-local.json
```

The DLL is often installed elsewhere. Discover the actual module path first; do not copy this example path blindly.

Export with startup capture:

```powershell
.\scripts\run.ps1 export --group "完整群名" `
  --capture .\anchors-local.json --capture-seconds 600 --wait-for-close
```

Add `--hours 48`, `--hours 72`, or explicit ISO-8601 `--start` and `--end`. Multiple accounts require `--account`; duplicate names require `--group-id` after presenting candidates to the user.

When the command reports that it is waiting, ask the user to exit WeChat from the tray. The program waits rather than killing it, spawns the same executable suspended, installs the validated hook, resumes it, and waits for the user to log into the same account. Do not request password, QR content, SMS codes, or phone-confirmation details.

After export, read every batch and author `report.json` using [SUMMARIZING.md](SUMMARIZING.md), then:

```powershell
.\scripts\run.ps1 render ".\outputs\本次目录"
```

## Read and cleanup invariants

- Original DB/WAL/SHM files are opened read-only. A double-read hash/stat/SHM check provides a stable optimistic snapshot; cross-database atomicity is not claimed.
- WAL magic, header checksum, salts, rolling frame checksums, transaction commit markers, and final DB size are verified. Stale or uncommitted tails are excluded explicitly.
- SQLCipher pages use 4096-byte pages and authenticated reserve data. Every page requested by SQLite must pass HMAC before decryption is returned.
- Decrypted pages exist only in the process-backed VFS. No plaintext SQLite database is written. Python/runtime copies or OS swap cannot be promised to have forensic-grade erasure.
- On error, close SQLite connections, dispose VFS objects, unload Frida, detach, and overwrite mutable key buffers as far as the runtime permits.

## Failure rules

- DLL hash or entry bytes differ: stop and regenerate/review the anchor.
- No HMAC-valid candidate: stop; never guess a key or report zero messages as success.
- A database key is missing: stop rather than export a partial shard set.
- Unknown schema or missing required columns: inspect actual schema and add an explicit adapter; do not guess field names.
- Snapshot changes repeatedly: stop and retry later rather than ignore WAL/SHM.
- Duplicate server IDs with different content: stop for investigation.
- PNG splitting: deliver every numbered part and its manifest; never silently crop.
