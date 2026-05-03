# Music Library Sync and Upgrade

Python tooling to turn **downloads → organized FLAC library**, keep **covers** sane, apply **overlay** patches, **embed** artwork when desired, optionally **sync to a portable**.

**Start here:** [USER_GUIDE.md](USER_GUIDE.md) · **Multi-disc / sidecar specifics:** [SIDECAR_RULES.md](SIDECAR_RULES.md) · **Install:** [PREREQUISITES.md](PREREQUISITES.md)

---

## Features

- **Downloads → library**: organizes into Artist/Album (and VOL/CD layouts from tags).
- **Artwork**: sidecars (`cover.jpg` / `folder.jpg`), web (CAA) where enabled, embedding.
- **Update overlay**: drop patches mirrored under `_UpdateOverlay`, applied into the library early in each run.
- **FLAC-only**: drops lossy cousins when FLAC is present for the same track.
- **Portable sync**: optional push to configured `T8_ROOT`.
- **Tray launcher**: queues the same `**main.py`** CLI.
- **Logging**: Windows defaults vary by Python install — see [Logging](#logging).

---

## Prerequisites

Python 3.11+, venv, `pip install -r requirements.txt` — see **[PREREQUISITES.md](PREREQUISITES.md)**.

---

## Configuration

Paths and behavior live in `**config.py`**, including:


| Setting                            | Typical role                            |
| ---------------------------------- | --------------------------------------- |
| `DOWNLOADS_DIR`                    | Incoming music staging area             |
| `MUSIC_ROOT`                       | Canonical library tree                  |
| `UPDATE_ROOT`                      | Overlay (**mirror `MUSIC_ROOT` paths**) |
| `BACKUP_ROOT`                      | Pre–embed FLAC snapshots                |
| `T8_ROOT`                          | Device share (optional)                 |
| `LOGS_DIR` / `SYNC_MUSIC_LOGS_DIR` | Logs on disk                            |


---

## Usage (canonical entry point)

Use `**main.py`** (not the legacy `**library_sync_and_upgrade*.py**` copies unless you maintain them yourself):

```bash
# Typical full run from repository root / sync-music-libraries folder
python main.py --mode normal

# Embed mode: overlay cover.jpg copied this run gets written into FLAC tags (after backup logic)
python main.py --mode embed

# Restore from backup mirror (+ sync workflow per mode)
python main.py --mode restore

# No writes
python main.py --mode normal --dry
```

**Tray:**

```bash
python library_tray_launcher.py
```

---

## Artwork overview

**Sidecars** (`cover.jpg` / `folder.jpg`) live **next to the audio** — album root for flat albums, or under `**CD*`** / `**VOL***` leaf folders when the library is laid out that way. **Embedded** pictures are handled in separate steps (`main.py` Steps 5+).

Rough rules (full detail → **SIDECAR_RULES.md**):

- **Flat album** → sidecars + embed use **album root** `cover.jpg`.
- **Folders like `VOL1/CD1**` → put art **inside the folder that holds the FLACs**.
- Album-root “box” CAA/embed attempt for multi-disc layouts is focused on albums **fresh from Downloads on that same run**; later runs leave an intentionally empty album root alone if neither root sidecar exists — see **SIDECAR_RULES** / **ensure_cover_and_folder** behavior.

Ambiguous Downloads artwork stays under Downloads until leaf sidecars complete; Step 10 uses paths registered during that import.

---

## Modes (`main.py`)


| Mode        | Typical use                                                                                                 |
| ----------- | ----------------------------------------------------------------------------------------------------------- |
| **normal**  | Downloads, overlay apply, FLAC-only, cover ensure, embed **if tags lack** art, overlay-driven embed omitted |
| **embed**   | Like normal, plus embedding **albums that gained `cover.jpg` from overlay this run** into FLAC files        |
| **restore** | Restore backed-up originals; embedding off                                                                  |


Hidden `**--embed-all**`: brute-force embed from `**cover.jpg` per walked folder — avoid unless you know you need it.

---

## Logging

Everything goes under `**config.LOGS_DIR**` (never under iCloud `scripts\`). Detail + summary filenames are platform-specific (e.g. `library_sync_detail_windows.log`).

**Windows**

| Python | Default log directory |
| ------ | ---------------------- |
| Normal install / typical venv (`python.org`) | `%LOCALAPPDATA%\sync-music-libraries\logs` |
| **Microsoft Store** Python (including many venvs whose `sys.base_executable` points at the Store runtime) | `%USERPROFILE%\.sync-music-libraries\logs` |

Store builds redirect writes under `%LOCALAPPDATA%` into an app-package `LocalCache`, so **Explorer and `cmd` could not see** logs at the “logical” path while Python could — using the dot-folder avoids that.

Override anytime: set **`SYNC_MUSIC_LOGS_DIR`** to an absolute folder (User env, Task Scheduler, or shell).

**Smoke test:** from a checkout of this repo, `python test_log_paths.py` prints the resolved paths and writes a line to the detail log (not copied by deploy).

**macOS:** `~/Library/Logs/sync-music-libraries/` (see `config.py`).

---

## Deploy to iCloud (Windows)

- **Source:** run **`deploy_to_icloud.bat` from the full repository root** (same folder as `sync_operations.py`, `artwork.py`, etc.). That directory is the copy **source** (`%~dp0`); nothing is pulled from `git` by path.
- **Target:** the iCloud `…\scripts\sync-music-libraries` folder may be **empty** — the script creates it and copies all listed files in. “Empty target” is not the problem.
- **What goes wrong:** if the **.bat is started from a folder that is not the full repo** (e.g. only `main.py` was copied there), the source files are missing and the deploy is incomplete. Always start the batch from your **complete** `C:\src\sync-music-libraries` (or equivalent) tree.

---

## Project layout (maintained codebase)

```
sync-music-libraries/
├── main.py                     # Canonical CLI entrypoint
├── config.py                   # Paths + toggles
├── library_tray_launcher.py    # Tray → runs main.py
├── artwork.py, file_operations.py, sync_operations.py, …
├── USER_GUIDE.md               # Typical workflow (lightweight)
├── SIDECAR_RULES.md            # Cover/folder/embed rules detail
├── PREREQUISITES.md            # Setup
├── requirements.txt
└── README.md                   # This file

# Legacy snapshots (prefer main.py flows):
├── library_sync_and_upgrade.py
└── library_sync_and_upgrade_updated.py
```

---

## License

Private project – all rights reserved.