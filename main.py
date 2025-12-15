#!/usr/bin/env python3
"""
Main entry point for music library sync and upgrade script.

Modes:
  --mode normal   : Process new downloads, updates overlay, embed missing art, enforce FLAC-only, sync to T8.
  --mode embed    : Same as normal, but ALSO embed cover.jpg from UPDATE overlay into FLACs (with backup).
  --mode restore  : Restore FLACs from backup and sync to T8.

Flags:
  --dry           : Dry-run. Log actions, but make no changes.
"""
import argparse
import sys

from artwork import (
    embed_art_into_flacs,
    embed_missing_art_global,
    fixup_missing_art,
    init_musicbrainz,
)
from file_operations import process_downloads, upgrade_albums_to_flac_only
from logging_utils import (
    add_global_warning,
    log,
    notify_run_summary,
    print_summary_log_to_stdout,
    setup_logging,
    show_summary_log_in_viewer,
    write_summary_log,
)
from sync_operations import (
    apply_updates_from_overlay,
    restore_flacs_from_backups,
    sync_music_to_t8,
    sync_update_root_structure,
)

# Global flags that control behavior
DRY_RUN = False
BACKUP_ORIGINAL_FLAC_BEFORE_EMBED = True
RESTORE_FROM_BACKUP_MODE = False

# Embedding behavior flags (overridden by command line arguments)
EMBED_IF_MISSING = False        # embed cover.jpg only into FLACs that currently lack embedded art
EMBED_FROM_UPDATES = False      # in embed mode, force embed for albums with cover.jpg from UPDATE_ROOT
EMBED_ALL = False               # advanced: embed cover.jpg into all FLACs in all albums


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Music Library Automation Script")
    parser.add_argument(
        "--mode",
        choices=["normal", "embed", "restore"],
        default="normal",
        help="Run mode: normal, embed (update embedded art using Update overlay), or restore (restore FLACs from backup)"
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="Dry run mode (no file modifications)"
    )
    parser.add_argument(
        "--embed-all",
        action="store_true",
        help=argparse.SUPPRESS
    )

    args = parser.parse_args()

    global DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED, RESTORE_FROM_BACKUP_MODE
    global EMBED_IF_MISSING, EMBED_FROM_UPDATES, EMBED_ALL

    DRY_RUN = args.dry
    EMBED_ALL = args.embed_all

    if args.mode == "normal":
        RESTORE_FROM_BACKUP_MODE = False
        BACKUP_ORIGINAL_FLAC_BEFORE_EMBED = True
        EMBED_IF_MISSING = True
        EMBED_FROM_UPDATES = False
    elif args.mode == "embed":
        RESTORE_FROM_BACKUP_MODE = False
        BACKUP_ORIGINAL_FLAC_BEFORE_EMBED = True
        EMBED_IF_MISSING = True
        EMBED_FROM_UPDATES = True
    elif args.mode == "restore":
        RESTORE_FROM_BACKUP_MODE = True
        BACKUP_ORIGINAL_FLAC_BEFORE_EMBED = False
        EMBED_IF_MISSING = False
        EMBED_FROM_UPDATES = False

    setup_logging()
    log(f"Starting script in mode: {args.mode}")
    log(f"DRY_RUN = {DRY_RUN}")
    log(f"EMBED_ALL = {EMBED_ALL}")

    init_musicbrainz()

    try:
        if RESTORE_FROM_BACKUP_MODE:
            restore_flacs_from_backups(DRY_RUN)
            sync_music_to_t8(DRY_RUN)
            log("Restore mode complete.")
            write_summary_log(args.mode, DRY_RUN)
            notify_run_summary(args.mode)
            return

        log("\nStep 1: Process new downloads (organize + art)...")
        process_downloads(DRY_RUN)

        log("\nStep 2: Apply UPDATE overlay (files from Update -> Music)...")
        updated_album_dirs, albums_with_new_cover = apply_updates_from_overlay(DRY_RUN)

        log("\nStep 3: Upgrade albums to FLAC-only where FLAC exists...")
        upgrade_albums_to_flac_only(DRY_RUN)

        log("\nStep 4: Embed missing artwork (only FLACs with no embedded art)...")
        embed_missing_art_global(DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED, EMBED_IF_MISSING)

        if EMBED_ALL:
            log("\n[EMBED ALL] Embedding cover.jpg into all FLACs in all albums (advanced mode).")
            from config import MUSIC_ROOT
            from pathlib import Path
            import os
            for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
                embed_art_into_flacs(Path(dirpath), DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED)

        if EMBED_FROM_UPDATES and albums_with_new_cover:
            log("\n[EMBED FROM UPDATES] Embedding new cover.jpg from UPDATE overlay into updated albums...")
            from logging_utils import album_label_from_dir, add_album_event_label
            for album_dir in sorted(albums_with_new_cover):
                log(f"  [EMBED FROM UPDATE] Album: {album_dir}")
                embed_art_into_flacs(album_dir, DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED)
                label = album_label_from_dir(album_dir)
                add_album_event_label(label, "Embedded new art from overlay.")

        log("\nStep 5: Sync master library to T8...")
        sync_music_to_t8(DRY_RUN)

        log("\nStep 6: Sync empty UPDATE overlay directory structure...")
        sync_update_root_structure(DRY_RUN)

        log("\nStep 7: Ensure artist images (folder.jpg and artist.jpg) in artist folders...")
        from artwork import ensure_artist_images
        from config import MUSIC_ROOT
        from pathlib import Path
        import os
        
        artist_dirs_processed = set()
        for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
            dir_path = Path(dirpath)
            # Check if this is an artist folder (has album subdirectories with audio files)
            has_albums = False
            for subdir in dir_path.iterdir():
                if subdir.is_dir():
                    # Check if subdir has audio files (it's an album)
                    for audio_file in subdir.iterdir():
                        if audio_file.is_file() and audio_file.suffix.lower() in {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav"}:
                            has_albums = True
                            break
                    if has_albums:
                        break
            
            # If this looks like an artist folder (parent of album folders), process it
            if has_albums and dir_path not in artist_dirs_processed:
                # Get artist name from folder name
                artist_name = dir_path.name
                if artist_name and artist_name != "Music":
                    ensure_artist_images(dir_path, artist_name, DRY_RUN)
                    artist_dirs_processed.add(dir_path)

        log("\nStep 8: Sync backup folder (remove identical backups, restore missing files)...")
        from sync_operations import sync_backups
        sync_backups(DRY_RUN)

        log("\nStep 9: Final missing-art fixup...")
        fixup_missing_art(DRY_RUN)

        log("\nStep 10: Writing summary log...")
        write_summary_log(args.mode, DRY_RUN)

        log("\nStep 11: Run summary notification...")
        notify_run_summary(args.mode)
               
        log("\nRun complete.")

        # Exit with appropriate code based on warnings/errors
        # Exit codes: 0 = clean (idle icon), 2 = warnings (yellow icon), 1 = errors (red icon)
        # Calculate exit code FIRST before doing any operations that might fail
        from logging_utils import ALBUM_SUMMARY, GLOBAL_WARNINGS
        total_warnings = sum(len(v["warnings"]) for v in ALBUM_SUMMARY.values()) + len(GLOBAL_WARNINGS)
        exit_code = 2 if total_warnings > 0 else 0
        
        # Print summary to console (may fail if not interactive, but that's OK)
        try:
            print_summary_log_to_stdout()
        except Exception as e:
            log(f"[WARN] Could not print summary log: {e}")
        
        # Keep console open for user to review
        # On Windows, always try to wait for input when run from console
        # When run from tray launcher, this will fail gracefully and exit immediately
        if sys.platform == "win32":
            try:
                # Try to wait for user input - this keeps console open
                # If stdin is not available (tray launcher), this will raise an exception
                log("\nPress Enter to close this window...")
                input()
            except (EOFError, KeyboardInterrupt, OSError, AttributeError):
                # stdin not available or interrupted - likely running from tray launcher
                # Just continue and exit (console will close automatically)
                pass
        
        # Log exit status and exit with appropriate code
        log(f"Exit status: {total_warnings} warning(s) found")
        if exit_code == 2:
            log("Exiting with code 2 (warnings) - systray will show yellow warning icon")
        else:
            log("Exiting with code 0 (success) - systray will show idle icon")
        sys.exit(exit_code)

    except Exception as e:
        from logging_utils import logger
        logger.exception("Fatal error during run")
        add_global_warning(f"Fatal error during run: {e}")
        write_summary_log(args.mode, DRY_RUN)
        notify_run_summary(args.mode)
        sys.exit(1)


if __name__ == "__main__":
    main()

