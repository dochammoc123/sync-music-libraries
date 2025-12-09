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

## Installation

1. Clone or download this repository
2. Create a virtual environment:
   ```bash
   python -m venv .venv
   ```
3. Activate the virtual environment:
   - Windows: `.venv\Scripts\activate`
   - macOS/Linux: `source .venv/bin/activate`
4. Install dependencies:
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
├── library_tray_launcher.py      # Tray launcher
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


