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
from config import MIN_DISK_CAPACITY_BYTES, MUSIC_ROOT, T8_ROOT, check_disk_capacity
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
    parser.add_argument(
        "--t8-checksums",
        action="store_true",
        help="Use MD5 checksums for T8 sync comparison (slower but more accurate). Default: use fast size+mtime comparison."
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

    setup_logging()  # Old API: file only (no console)
    from structured_logging import setup_detail_logging
    setup_detail_logging()  # New API: detail file + console
    
    def exit_with_error(error_msg: str, exit_code: int = 1, is_error: bool = True) -> None:
        """Common handler for early exits: log, write summary, notify, prompt, exit.
        
        Args:
            error_msg: Error message (may contain "ERROR:" prefix which will be stripped)
            exit_code: Exit code (default 1)
            is_error: If True, log as error; if False, log as warning (e.g., for DRY_RUN scenarios)
        """
        log(error_msg)
        # Strip "ERROR:" or "WARNING:" prefix if present
        clean_msg = error_msg
        if clean_msg.startswith("ERROR:") or clean_msg.startswith("WARNING:"):
            clean_msg = clean_msg.split(":", 1)[1].lstrip()
        # Log to structured logging detail log
        from structured_logging import logmsg
        if is_error:
            logmsg.error(clean_msg)
        else:
            logmsg.warn(clean_msg)
        # Also add to old API global warnings for old summary file
        level = "error" if is_error else "warn"
        add_global_warning(clean_msg, level=level)
        write_summary_log(args.mode, DRY_RUN)
        logmsg.write_summary(args.mode, DRY_RUN)
        notify_run_summary(args.mode)
        # Keep console open for user to review
        if sys.platform == "win32":
            try:
                print()  # Add blank line before prompt
                print("Press Enter to close this window...")  # Use print() not log() - log() doesn't write to console
                input()
            except (EOFError, KeyboardInterrupt, OSError, AttributeError):
                pass  # stdin not available - likely running from tray launcher
        sys.exit(exit_code)
    
    log(f"Starting script in mode: {args.mode}")
    log(f"DRY_RUN = {DRY_RUN}")
    log(f"EMBED_ALL = {EMBED_ALL}")
    log(f"T8_SYNC_USE_CHECKSUMS = {args.t8_checksums}")

    # Safety check: Verify both ROON and T8 drives have at least 1TB total capacity
    log("\nSafety check: Verifying disk capacity on target drives...")
    min_tb = MIN_DISK_CAPACITY_BYTES / (1024 ** 4)  # Convert to TB for display
    
    try:
        # Check ROON (MUSIC_ROOT) drive
        log(f"  Checking ROON drive: {MUSIC_ROOT}")
        has_capacity, capacity_gb, checked_path = check_disk_capacity(MUSIC_ROOT, MIN_DISK_CAPACITY_BYTES)
        if not has_capacity:
            # Treat capacity <= 1 GB as "unknown" (likely a network share that can't report capacity reliably)
            # This prevents false positives where network shares return 0 or very small values
            if capacity_gb <= 1.0:
                # Capacity unknown (network shares may not report capacity reliably)
                # Check if path is at least accessible
                try:
                    test_access = MUSIC_ROOT if MUSIC_ROOT.exists() else MUSIC_ROOT.parent
                    if test_access.exists():
                        log(f"  WARNING: ROON drive ({checked_path}) capacity could not be determined, but path is accessible.")
                        log(f"  Allowing operation (network shares may not report capacity reliably).")
                        add_global_warning(f"ROON drive capacity check inconclusive - path accessible but capacity unknown")
                    else:
                        # Path not accessible
                        error_msg = (
                            f"ERROR: Could not verify disk capacity for ROON drive ({checked_path}).\n"
                            f"  The drive appears to be inaccessible.\n"
                            f"  Required: {min_tb:.2f} TB minimum total capacity.\n"
                            f"This check protects system drives on the server. Exiting."
                        )
                        exit_with_error(error_msg)
                except Exception as e:
                    # In dry-run, allow it to continue with warning (might be a temporary network issue)
                    if DRY_RUN:
                        log(f"  WARNING: Could not access ROON drive ({checked_path}) in DRY RUN: {e}")
                        log(f"  DRY RUN: Continuing with warning (path may be temporarily inaccessible).")
                        add_global_warning(f"ROON drive access check failed in DRY RUN - continuing with warning: {e}")
                    else:
                        error_msg = (
                            f"ERROR: Could not access ROON drive ({checked_path}): {e}\n"
                            f"  Required: {min_tb:.2f} TB minimum total capacity.\n"
                            f"This check protects system drives on the server. Exiting."
                        )
                        exit_with_error(error_msg)
            else:
                # Drive too small (likely a system drive) - only fail if > 1GB (real capacity, not unknown)
                # In dry-run mode, allow it to continue with warning (no changes will be made anyway)
                if DRY_RUN:
                    log(f"  WARNING: ROON drive ({checked_path}) capacity is too small.")
                    log(f"  Required: {min_tb:.2f} TB minimum")
                    log(f"  Actual: {capacity_gb:.2f} GB ({capacity_gb / 1024:.2f} TB)")
                    log(f"  DRY RUN: Allowing operation to continue (no changes will be made).")
                    add_global_warning(f"ROON drive capacity too small ({capacity_gb:.2f} GB) - continuing in DRY RUN mode")
                else:
                    error_msg = (
                        f"ERROR: ROON drive ({checked_path}) capacity is too small.\n"
                        f"  Required: {min_tb:.2f} TB minimum\n"
                        f"  Actual: {capacity_gb:.2f} GB ({capacity_gb / 1024:.2f} TB)\n"
                        f"This check protects system drives on the server. Exiting."
                    )
                    exit_with_error(error_msg)
        else:
            log(f"  ROON drive ({checked_path}): {capacity_gb:.2f} GB capacity ({capacity_gb / 1024:.2f} TB) - OK")
        
        # Check T8 drive
        if T8_ROOT is not None:
            log(f"  Checking T8 drive: {T8_ROOT}")
            has_capacity, capacity_gb, checked_path = check_disk_capacity(T8_ROOT, MIN_DISK_CAPACITY_BYTES)
            if not has_capacity:
                # Treat capacity <= 1 GB as "unknown" (likely a network share that can't report capacity reliably)
                # This prevents false positives where network shares return 0 or very small values
                if capacity_gb <= 1.0:
                    # Capacity unknown (network shares may not report capacity reliably)
                    # Check if path is at least accessible
                    try:
                        test_access = T8_ROOT if T8_ROOT.exists() else T8_ROOT.parent
                        if test_access.exists():
                            log(f"  WARNING: T8 drive ({checked_path}) capacity could not be determined, but path is accessible.")
                            log(f"  Allowing operation (network shares may not report capacity reliably).")
                            add_global_warning(f"T8 drive capacity check inconclusive - path accessible but capacity unknown")
                        else:
                            # Path not accessible - in dry-run, allow with warning (for testing when T8 is offline)
                            if DRY_RUN:
                                log(f"  WARNING: T8 drive ({checked_path}) appears to be inaccessible.")
                                log(f"  DRY RUN: Continuing with warning (drive may be offline or IP changed).")
                                add_global_warning(f"T8 drive inaccessible in DRY RUN - continuing with warning (drive may be offline)")
                            else:
                                error_msg = (
                                    f"ERROR: Could not verify disk capacity for T8 drive ({checked_path}).\n"
                                    f"  The drive appears to be inaccessible.\n"
                                    f"  Required: {min_tb:.2f} TB minimum total capacity.\n"
                                    f"This check protects system drives on the server. Exiting."
                                )
                                exit_with_error(error_msg)
                    except Exception as e:
                        # In dry-run, allow it to continue with warning (might be a temporary network issue)
                        if DRY_RUN:
                            log(f"  WARNING: Could not access T8 drive ({checked_path}) in DRY RUN: {e}")
                            log(f"  DRY RUN: Continuing with warning (path may be temporarily inaccessible).")
                            add_global_warning(f"T8 drive access check failed in DRY RUN - continuing with warning: {e}")
                        else:
                            error_msg = (
                                f"ERROR: Could not access T8 drive ({checked_path}): {e}\n"
                                f"  Required: {min_tb:.2f} TB minimum total capacity.\n"
                                f"This check protects system drives on the server. Exiting."
                            )
                            exit_with_error(error_msg)
                else:
                    # Drive too small (likely a system drive) - only fail if > 1GB (real capacity, not unknown)
                    # In dry-run mode, allow it to continue with warning (no changes will be made anyway)
                    if DRY_RUN:
                        log(f"  WARNING: T8 drive ({checked_path}) capacity is too small.")
                        log(f"  Required: {min_tb:.2f} TB minimum")
                        log(f"  Actual: {capacity_gb:.2f} GB ({capacity_gb / 1024:.2f} TB)")
                        log(f"  DRY RUN: Allowing operation to continue (no changes will be made).")
                        add_global_warning(f"T8 drive capacity too small ({capacity_gb:.2f} GB) - continuing in DRY RUN mode")
                    else:
                        error_msg = (
                            f"ERROR: T8 drive ({checked_path}) capacity is too small.\n"
                            f"  Required: {min_tb:.2f} TB minimum\n"
                            f"  Actual: {capacity_gb:.2f} GB ({capacity_gb / 1024:.2f} TB)\n"
                            f"This check protects system drives on the server. Exiting."
                        )
                        exit_with_error(error_msg)
            else:
                log(f"  T8 drive ({checked_path}): {capacity_gb:.2f} GB capacity ({capacity_gb / 1024:.2f} TB) - OK")
        
        log("Disk capacity check passed.\n")
    except Exception as e:
        error_msg = f"ERROR: Exception during disk capacity check: {e}"
        from logging_utils import logger
        logger.exception("Disk capacity check failed")
        exit_with_error(error_msg)

    init_musicbrainz()

    try:
        if RESTORE_FROM_BACKUP_MODE:
            restore_flacs_from_backups(DRY_RUN)
            sync_music_to_t8(DRY_RUN, use_checksums=args.t8_checksums)
            log("Restore mode complete.")
            
            log("\nRefresh ROON library...")
            from roon_refresh import refresh_roon_library
            roon_refresh_success = refresh_roon_library(DRY_RUN)
            if not roon_refresh_success:
                add_global_warning("ROON library refresh failed - you may need to manually restart ROON to see new files")
            
            write_summary_log(args.mode, DRY_RUN)
            from structured_logging import logmsg
            logmsg.write_summary(args.mode, DRY_RUN)
            notify_run_summary(args.mode)
            
            # Calculate exit code
            from logging_utils import ALBUM_SUMMARY, GLOBAL_WARNINGS
            total_warnings = sum(len(v["warnings"]) for v in ALBUM_SUMMARY.values()) + len(GLOBAL_WARNINGS)
            exit_code = 2 if total_warnings > 0 else 0
            
            # Print summary to console (before "Press Enter" prompt so user can review it)
            try:
                print_summary_log_to_stdout()
            except Exception as e:
                log(f"[WARN] Could not print summary log: {e}")
            
            # Log exit status before prompt
            log(f"Exit status: {total_warnings} warning(s) found")
            if exit_code == 2:
                log("Exiting with code 2 (warnings) - systray will show yellow warning icon")
            else:
                log("Exiting with code 0 (success) - systray will show idle icon")
            
            # Keep console open for user to review
            if sys.platform == "win32":
                try:
                    print()  # Add blank line before prompt
                    print("Press Enter to close this window...")  # Use print() not log() - log() doesn't write to console
                    input()
                except (EOFError, KeyboardInterrupt, OSError, AttributeError):
                    pass
            
            sys.exit(exit_code)

        # Step 1: Process new downloads (organize + art)
        log("\nStep 1: Process new downloads (organize + art)...")
        from structured_logging import logmsg
        # Step header processes MULTIPLE albums (each album gets its own instance)
        header_key = logmsg.set_header("Step 1: Process new downloads", "%msg% (%count% items)")
        process_downloads(DRY_RUN)

        log("\nStep 2: Apply UPDATE overlay (files from Update -> Music)...")
        header_key = logmsg.set_header("Step 2: Apply UPDATE overlay", "%msg% (%count% items)", key=header_key)
        updated_album_dirs, albums_with_new_cover = apply_updates_from_overlay(DRY_RUN)

        log("\nStep 3: Upgrade albums to FLAC-only where FLAC exists...")
        header_key = logmsg.set_header("Step 3: Upgrade albums to FLAC-only", "%msg% (%count% items)", key=header_key)
        upgrade_albums_to_flac_only(DRY_RUN)

        log("\nStep 4: Embed missing artwork (only FLACs with no embedded art)...")
        header_key = logmsg.set_header("Step 4: Embed missing artwork", "%msg% (%count% items)", key=header_key)
        embed_missing_art_global(DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED, EMBED_IF_MISSING)

        if EMBED_ALL:
            log("\n[EMBED ALL] Embedding cover.jpg into all FLACs in all albums (advanced mode).")
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
        header_key = logmsg.set_header("Step 5: Sync master library to T8", "%msg% (%count% items)", key=header_key)
        sync_music_to_t8(DRY_RUN, use_checksums=args.t8_checksums)

        log("\nStep 6: Sync empty UPDATE overlay directory structure...")
        header_key = logmsg.set_header("Step 6: Sync empty UPDATE overlay directory structure", "%msg%", key=header_key)
        sync_update_root_structure(DRY_RUN)

        log("\nStep 7: Ensure artist images (folder.jpg and artist.jpg) in artist folders...")
        header_key = logmsg.set_header("Step 7: Ensure artist images", "%msg% (%count% artists)", key=header_key)
        from artwork import ensure_artist_images
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
        header_key = logmsg.set_header("Step 8: Sync backup folder", "%msg% (%count% items)", key=header_key)
        from sync_operations import sync_backups
        sync_backups(DRY_RUN)

        log("\nStep 9: Final missing-art fixup...")
        header_key = logmsg.set_header("Step 9: Final missing-art fixup", "%msg% (%count% items)", key=header_key)
        fixup_missing_art(DRY_RUN)

        log("\nStep 10: Refresh ROON library...")
        header_key = logmsg.set_header("Step 10: Refresh ROON library", "%msg%", key=header_key)
        from roon_refresh import refresh_roon_library
        roon_refresh_success = refresh_roon_library(DRY_RUN)
        if not roon_refresh_success:
            add_global_warning("ROON library refresh failed - you may need to manually restart ROON to see new files")

        log("\nStep 11: Writing summary log...")
        # Write old API summary (for compatibility during migration)
        write_summary_log(args.mode, DRY_RUN)
        # Write new structured summary
        from structured_logging import logmsg
        logmsg.write_summary(args.mode, DRY_RUN)

        log("\nStep 12: Run summary notification...")
        notify_run_summary(args.mode)
               
        log("\nRun complete.")

        # Exit with appropriate code based on warnings/errors
        # Exit codes: 0 = clean (idle icon), 2 = warnings (yellow icon), 1 = errors (red icon)
        # Calculate exit code FIRST before doing any operations that might fail
        from logging_utils import ALBUM_SUMMARY, GLOBAL_WARNINGS
        total_warnings = sum(len(v["warnings"]) for v in ALBUM_SUMMARY.values()) + len(GLOBAL_WARNINGS)
        exit_code = 2 if total_warnings > 0 else 0
        
        # Summary is already printed by logmsg.write_summary() (new API)
        # Old summary printing is now redundant but kept for compatibility
        
        # Log exit status before prompt
        log(f"Exit status: {total_warnings} warning(s) found")
        if exit_code == 2:
            log("Exiting with code 2 (warnings) - systray will show yellow warning icon")
        else:
            log("Exiting with code 0 (success) - systray will show idle icon")
        
        # Keep console open for user to review
        # On Windows, always try to wait for input when run from console
        # When run from tray launcher, this will fail gracefully and exit immediately
        if sys.platform == "win32":
            try:
                # Try to wait for user input - this keeps console open
                # If stdin is not available (tray launcher), this will raise an exception
                print()  # Add blank line before prompt
                print("Press Enter to close this window...")  # Use print() not log() - log() doesn't write to console
                input()
            except (EOFError, KeyboardInterrupt, OSError, AttributeError):
                # stdin not available or interrupted - likely running from tray launcher
                # Just continue and exit (console will close automatically)
                pass
        
        sys.exit(exit_code)

    except Exception as e:
        from logging_utils import logger
        logger.exception("Fatal error during run")
        error_msg = f"Fatal error during run: {e}"
        exit_with_error(error_msg)


if __name__ == "__main__":
    main()

