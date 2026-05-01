# Music Library Sync and Upgrade

A comprehensive Python script for organizing, syncing, and managing music libraries with automatic artwork handling, FLAC-only enforcement, and cross-platform support.

## Features

- **Automatic Organization**: Organizes downloaded music files into structured library format (Artist/Album)
- **Artwork Management**: Automatically finds, embeds, and manages album artwork
- **FLAC-Only Enforcement**: Removes non-FLAC files when FLAC versions exist
- **Update Overlay System**: Apply patches and updates via overlay directory
- **T8 Library Sync**: Syncs master library to T8 destination
- **Cross-Platform**: Works on Windows, macOS, and Linux
- **Tray Launcher**: System tray application for easy access
- **Comprehensive Logging**: Detailed logs with summary reports

## Prerequisites

**⚠️ IMPORTANT: See [PREREQUISITES.md](PREREQUISITES.md) for detailed setup instructions.**

Before using this script, you need:

- Python 3.11 or later
- Virtual environment set up at:
  - Windows: `C:\Users\docha\local_python_envs\t8sync`
  - macOS: `~/local_python_envs/t8sync`
- All dependencies installed (see PREREQUISITES.md)

## Installation

1. Clone or download this repository
2. **Set up virtual environment** (see [PREREQUISITES.md](PREREQUISITES.md)):
  ```bash
   # Windows
   cd C:\Users\docha\local_python_envs
   python -m venv t8sync

   # macOS
   cd ~/local_python_envs
   python3 -m venv t8sync
  ```
3. **Activate the virtual environment:**
  - Windows: `C:\Users\docha\local_python_envs\t8sync\Scripts\activate`
  - macOS: `source ~/local_python_envs/t8sync/bin/activate`
4. **Install dependencies:**
  ```bash
   pip install -r requirements.txt
  ```

## Configuration

Edit the configuration section in `config.py` to set your paths:

- `DOWNLOADS_DIR`: Where new music downloads are located
- `MUSIC_ROOT`: Your main music library root
- `T8_ROOT`: Destination for T8 sync (optional)
- `UPDATE_ROOT`: Overlay directory for updates
- `BACKUP_ROOT`: Backup location for original FLACs

## Artwork sidecar rules

**Sidecar artwork** means `cover.jpg` and `folder.jpg` **next to your tracks** (album folder and/or disc subfolders such as `CD1/` or `VOL1/CD2/`). That is separate from *embedded* art inside the files.

Plain rules:

- **Tracks only in the album folder** (**no** `CD*`/`VOL*` layout leaves detected) → **simple album**: both images at **album root** for cleanup completeness; embed from **`album_root/cover.jpg` only**.
- **Tracks live under leaf-shaped folders — even once** (**`CD1` only, `VOL3` only, `VOL1` only, lone `VOL1/CD2`, Lilith layouts, …**) → **same rule family as multi-disc everywhere**: Downloads auto-art only when image candidates collapse cleanly; guarded root vs leaf behavior in Step 4; **embedding** skips the whole album until **every leaf folder you actually have** has **at least** `cover.jpg` **or** `folder.jpg`; cleanup completeness wants **both** files **inside each** leaf (**one leaf ⇒ just that folder**). Album root artwork does **not** satisfy leaf completeness.
- **`Vol 2`**-style naming in folders can tweak warnings; **what matters is folder shape**, not whether you personally “meant” a single-disc release.

**Embedding (Step 5) short version:** Runs in **normal** / **embed** (not restore). Copies from **the same folder as each track** (`cover.jpg` preferred, else `folder.jpg`); **never** silently uses album root art for tracks under `CD2/` etc. **`--mode embed`** can also force-update from overlay; **`--embed-all`** is a blunt-force option.

Ambiguous download artwork: Step 10 skips cleanup for **paths recorded during that album’s Step 1 import** (filesystem layout, not tag-based re-matching). See **“How Step 10 knows what to keep”** in [SIDECAR_RULES.md](SIDECAR_RULES.md).

See that file for full step-by-step detail (Downloads hints, Step 4, CAA, lifting preservation when the library is leaf-complete).

## Usage

### Command Line

```bash
# Normal mode (process downloads, sync, embed missing art)
python library_sync_and_upgrade.py --mode normal

# Embed mode (also embed cover.jpg from UPDATE overlay)
python library_sync_and_upgrade.py --mode embed

# Restore mode (restore FLACs from backup)
python library_sync_and_upgrade.py --mode restore

# Dry run (no changes, just log what would happen)
python library_sync_and_upgrade.py --mode normal --dry
```

### Tray Launcher

Run the tray launcher for easy access:

```bash
python library_tray_launcher.py
```

Right-click the tray icon to access:

- Run (normal/embed/restore)
- DRY Run options
- Quit

## Modes

- **normal**: Process new downloads, apply updates, embed missing art, enforce FLAC-only, sync to T8
- **embed**: Same as normal, but also embed cover.jpg from UPDATE overlay into FLACs
- **restore**: Restore FLACs from backup and sync to T8

## Project Structure

```
sync-music-libraries/
├── library_sync_and_upgrade.py  # Main script (legacy, being refactored)
├── library_sync_and_upgrade_updated.py  # Has wip for logging refactor and some fixes (merged)
├── library_tray_launcher.py      # Tray launcher (live)
├── library_tray_launcher_updated.py # Some changes (merged into live)
├── config.py                     # Configuration module
├── logging_utils.py              # Logging utilities
├── file_operations.py            # File operations
├── tag_operations.py             # Tag reading/writing
├── artwork.py                    # Artwork handling
├── sync_operations.py            # Sync operations
├── main.py                       # Entry point
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

## Logging

Logs are written to:

- Detailed log: `{SCRIPTS_ROOT}/Logs/library_sync_{platform}.log`
- Summary log: `{SCRIPTS_ROOT}/Logs/library_sync_{platform}_summary.log`

## License

Private project - All rights reserved