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
from pathlib import Path

from artwork import (
    embed_art_into_audio_files,
    embed_missing_art_global,
    ensure_cover_and_folder_global,
    init_musicbrainz,
)
from config import (
    BACKUP_ROOT,
    DOWNLOADS_DIR,
    MIN_DISK_CAPACITY_BYTES,
    MUSIC_ROOT,
    SYSTEM,
    T8_ROOT,
    UPDATE_ROOT,
    check_disk_capacity,
)
from file_operations import process_downloads, upgrade_albums_to_flac_only
from logging_utils import (
    notify_run_summary,
    show_summary_log_in_viewer,
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

    from structured_logging import setup_detail_logging
    setup_detail_logging()  # Detail file + console
    
    # Add run divider to detail log file (use public API)
    from structured_logging import logmsg
    from datetime import datetime
    divider = "=" * 80
    logmsg.verbose(divider)
    logmsg.verbose(f"New run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logmsg.verbose(divider)
    
    def exit_with_error(error_msg: str, exit_code: int = 1, is_error: bool = True) -> None:
        """Common handler for early exits: log, write summary, notify, prompt, exit.
        
        Args:
            error_msg: Error message (may contain "ERROR:" prefix which will be stripped)
            exit_code: Exit code (default 1)
            is_error: If True, log as error; if False, log as warning (e.g., for DRY_RUN scenarios)
        """
        # Strip "ERROR:" or "WARNING:" prefix if present
        clean_msg = error_msg
        if clean_msg.startswith("ERROR:") or clean_msg.startswith("WARNING:"):
            clean_msg = clean_msg.split(":", 1)[1].lstrip()
        # Log to structured logging (console + detail log)
        from structured_logging import logmsg
        if is_error:
            logmsg.error(clean_msg)
        else:
            logmsg.warn(clean_msg)
        logmsg.write_summary(args.mode, DRY_RUN)
        notify_run_summary(args.mode)
        # Keep console open for user to review
        if sys.platform == "win32":
            try:
                logmsg.info("")
                logmsg.info("Press Enter to close this window...")
                input()
            except (EOFError, KeyboardInterrupt, OSError, AttributeError):
                pass  # stdin not available - likely running from tray launcher
        sys.exit(exit_code)
    
    # Check permissions on all required directories before proceeding
    def check_directory_permissions(path: Path, name: str, required: bool = True, must_exist: bool = False) -> None:
        """
        Check read, write, and remove permissions on a directory.
        Exits with error if permissions are insufficient.
        
        Args:
            path: Directory path to check
            name: Human-readable name for error messages
            required: If False and path is None, skip check
            must_exist: If True, directory must exist; if False, will try to create it
        """
        if not required and path is None:
            return
        
        if path is None:
            error_msg = f"ERROR: {name} path is not configured (None)."
            exit_with_error(error_msg, exit_code=1, is_error=True)
        
        logmsg.verbose(f"Checking permissions for {name}: {path}")
        
        # Check if directory exists or can be created
        if not path.exists():
            if must_exist:
                error_msg = (
                    f"ERROR: {name} directory does not exist: {path}\n"
                    f"  Please create the directory or verify the path is correct."
                )
                exit_with_error(error_msg, exit_code=1, is_error=True)
            
            # Try to create the directory to test write access
            try:
                path.mkdir(parents=True, exist_ok=True)
                logmsg.verbose("  [OK] Directory created: OK")
            except (OSError, PermissionError) as e:
                error_msg = (
                    f"ERROR: Cannot access {name} directory: {path}\n"
                    f"  Directory does not exist and cannot be created: {e}\n"
                    f"  Please verify the path is correct and you have write permissions."
                )
                exit_with_error(error_msg, exit_code=1, is_error=True)
        
        # Test read access (list directory) - only if directory exists
        if path.exists():
            try:
                list(path.iterdir())
                logmsg.verbose("  [OK] Read access: OK")
            except (OSError, PermissionError) as e:
                error_msg = (
                    f"ERROR: Cannot read from {name} directory: {path}\n"
                    f"  Permission denied: {e}\n"
                    f"  Please verify you have read permissions."
                )
                exit_with_error(error_msg, exit_code=1, is_error=True)
        
        # Test write access (create a test file)
        test_file = path / ".permission_test"
        try:
            test_file.write_text("test")
            logmsg.verbose("  [OK] Write access: OK")
        except (OSError, PermissionError) as e:
            error_msg = (
                f"ERROR: Cannot write to {name} directory: {path}\n"
                f"  Permission denied: {e}\n"
                f"  Please verify you have write permissions."
            )
            exit_with_error(error_msg, exit_code=1, is_error=True)
        
        # Test remove access (delete the test file)
        try:
            test_file.unlink()
            logmsg.verbose("  [OK] Remove access: OK")
        except (OSError, PermissionError) as e:
            error_msg = (
                f"ERROR: Cannot remove files from {name} directory: {path}\n"
                f"  Permission denied: {e}\n"
                f"  Please verify you have delete permissions."
            )
            exit_with_error(error_msg, exit_code=1, is_error=True)
    
    logmsg.info("Safety check: Verifying directory permissions...")
    logmsg.verbose("Verifying directory permissions...")
    check_directory_permissions(MUSIC_ROOT, "MUSIC_ROOT (ROON)", must_exist=True)
    check_directory_permissions(BACKUP_ROOT, "BACKUP_ROOT", must_exist=False)  # May not exist yet
    # If BACKUP_ROOT exists but is empty, remove it (Step 8 will recreate if needed)
    if BACKUP_ROOT.exists():
        try:
            # Check if directory is empty
            if not any(BACKUP_ROOT.iterdir()):
                logmsg.verbose("Removing empty backup root: {path}", path=str(BACKUP_ROOT))
                BACKUP_ROOT.rmdir()
        except (OSError, PermissionError) as e:
            # If we can't remove it, that's OK - Step 8 will handle it
            logmsg.verbose("Could not remove empty backup root (will be handled in Step 8): {error}", error=str(e))
    check_directory_permissions(DOWNLOADS_DIR, "DOWNLOADS_DIR", must_exist=False)  # May not exist yet
    check_directory_permissions(UPDATE_ROOT, "UPDATE_ROOT", must_exist=False)  # May not exist yet
    if T8_ROOT is not None:
        check_directory_permissions(T8_ROOT, "T8_ROOT", must_exist=False)  # May not exist yet
    logmsg.info("Directory permissions check passed.")
    logmsg.verbose("Directory permissions check passed.")
    
    # Log startup info as always_show headers (appears in summary at end; no duplicate print)
    from structured_logging import logmsg
    header_key = logmsg.header(f"Starting script in mode: {args.mode}", always_show=True, key=None)
    try:
        header_key = logmsg.header(f"DRY_RUN = {DRY_RUN}", always_show=True, key=header_key)
        header_key = logmsg.header(f"EMBED_ALL = {EMBED_ALL}", always_show=True, key=header_key)
        header_key = logmsg.header(f"T8_SYNC_USE_CHECKSUMS = {args.t8_checksums}", always_show=True, key=header_key)
    finally:
        # Clear header stack so Step 1 can start fresh with key=None (prevents wrong header context for any logs between)
        logmsg.header(None, key=header_key)

    # Safety check: Verify both ROON and T8 drives have at least 1TB total capacity
    logmsg.info("Safety check: Verifying disk capacity on target drives...")
    min_tb = MIN_DISK_CAPACITY_BYTES / (1024 ** 4)  # Convert to TB for display
    
    try:
        # Check ROON (MUSIC_ROOT) drive
        logmsg.info("Checking ROON drive: {path}", path=str(MUSIC_ROOT))
        # Check if this is a network share - they often report incorrect capacity
        is_network_share = (
            (SYSTEM == "Windows" and str(MUSIC_ROOT).startswith("\\\\")) or
            (SYSTEM == "Darwin" and str(MUSIC_ROOT).startswith("SMB:"))
        )
        
        if is_network_share:
            # Network shares often report incorrect capacity - just check if path is accessible
            try:
                test_access = MUSIC_ROOT if MUSIC_ROOT.exists() else MUSIC_ROOT.parent
                if test_access.exists():
                    logmsg.info("INFO: ROON drive ({path}) is accessible (network share - capacity check skipped)", path=str(MUSIC_ROOT))
                    logmsg.verbose("ROON drive is a network share - capacity check skipped (network shares may not report capacity reliably)")
                    # Don't check capacity for network shares - they're unreliable
                    pass
                else:
                    # Path not accessible
                    if DRY_RUN:
                        logmsg.warn("WARNING: ROON drive ({path}) appears to be inaccessible.", path=str(MUSIC_ROOT))
                        logmsg.warn("DRY RUN: Continuing with warning (drive may be offline).")
                        logmsg.warn("ROON drive inaccessible in DRY RUN - continuing with warning (drive may be offline)")
                    else:
                        error_msg = (
                            f"ERROR: ROON drive ({MUSIC_ROOT}) is not accessible.\n"
                            f"Path may be inaccessible or drive may be offline.\n"
                            f"Please verify the ROON drive is accessible and try again."
                        )
                        exit_with_error(error_msg, exit_code=1, is_error=True)
            except Exception as e:
                # In dry-run, allow it to continue with warning (might be a temporary network issue)
                if DRY_RUN:
                    logmsg.warn("WARNING: Could not access ROON drive ({path}) in DRY RUN: {error}", path=str(MUSIC_ROOT), error=str(e))
                    logmsg.warn("DRY RUN: Continuing with warning (path may be temporarily inaccessible).")
                    logmsg.warn("ROON drive access check failed in DRY RUN - continuing with warning: {error}", error=str(e))
                else:
                    error_msg = (
                        f"ERROR: Could not access ROON drive ({MUSIC_ROOT}): {e}\n"
                        f"Please verify the ROON drive is accessible and try again."
                    )
                    exit_with_error(error_msg, exit_code=1, is_error=True)
        else:
            # Not a network share - do capacity check
            has_capacity, capacity_gb, checked_path = check_disk_capacity(MUSIC_ROOT, MIN_DISK_CAPACITY_BYTES)
            if has_capacity:
                logmsg.info("ROON drive ({path}): {capacity_gb:.2f} GB capacity ({tb:.2f} TB) - OK", path=str(checked_path), capacity_gb=capacity_gb, tb=capacity_gb / 1024)
            else:
                # Capacity check failed
                logmsg.warn("WARNING: ROON drive ({path}) capacity is too small.", path=str(checked_path))
                if DRY_RUN:
                    logmsg.warn("Required: {min_tb:.2f} TB minimum", min_tb=min_tb)
                    logmsg.warn("Actual: {capacity_gb:.2f} GB ({tb:.2f} TB)", capacity_gb=capacity_gb, tb=capacity_gb / 1024)
                    logmsg.warn("DRY RUN: Allowing operation to continue (no changes will be made).")
                    logmsg.warn("ROON drive capacity too small ({capacity} GB) - continuing in DRY RUN mode", capacity=capacity_gb)
                else:
                    error_msg = (
                        f"ERROR: ROON drive ({checked_path}) capacity is too small.\n"
                        f"  Required: {min_tb:.2f} TB minimum\n"
                        f"  Actual: {capacity_gb:.2f} GB ({capacity_gb / 1024:.2f} TB)\n"
                        f"This check protects system drives on the server. Exiting."
                    )
                    exit_with_error(error_msg, exit_code=1, is_error=True)
        
        # Check T8 drive
        if T8_ROOT is not None:
            logmsg.info("Checking T8 drive: {path}", path=str(T8_ROOT))
            # Check if this is a network share - they often report incorrect capacity
            is_network_share = (
                (SYSTEM == "Windows" and str(T8_ROOT).startswith("\\\\")) or
                (SYSTEM == "Darwin" and str(T8_ROOT).startswith("SMB:"))
            )
            
            if is_network_share:
                # Network shares often report incorrect capacity - just check if path is accessible
                try:
                    test_access = T8_ROOT if T8_ROOT.exists() else T8_ROOT.parent
                    if test_access.exists():
                        logmsg.info("INFO: T8 drive ({path}) is accessible (network share - capacity check skipped)", path=str(T8_ROOT))
                        logmsg.verbose("T8 drive is a network share - capacity check skipped (network shares may not report capacity reliably)")
                        # Don't check capacity for network shares - they're unreliable
                        pass
                    else:
                        # Path not accessible - in dry-run, allow with warning (for testing when T8 is offline)
                        if DRY_RUN:
                            logmsg.warn("WARNING: T8 drive ({path}) appears to be inaccessible.", path=str(T8_ROOT))
                            logmsg.warn("DRY RUN: Continuing with warning (drive may be offline or IP changed).")
                            logmsg.warn("T8 drive inaccessible in DRY RUN - continuing with warning (drive may be offline)")
                        else:
                            error_msg = (
                                f"ERROR: T8 drive ({T8_ROOT}) is not accessible.\n"
                                f"Path may be inaccessible or drive may be offline.\n"
                                f"Please verify the T8 drive is accessible and try again."
                            )
                            exit_with_error(error_msg, exit_code=1, is_error=True)
                except Exception as e:
                    # In dry-run, allow it to continue with warning (might be a temporary network issue)
                    if DRY_RUN:
                        logmsg.warn("WARNING: Could not access T8 drive ({path}) in DRY RUN: {error}", path=str(T8_ROOT), error=str(e))
                        logmsg.warn("DRY RUN: Continuing with warning (path may be temporarily inaccessible).")
                        logmsg.warn("T8 drive access check failed in DRY RUN - continuing with warning: {error}", error=str(e))
                    else:
                        error_msg = (
                            f"ERROR: Could not access T8 drive ({T8_ROOT}): {e}\n"
                            f"Please verify the T8 drive is accessible and try again."
                        )
                        exit_with_error(error_msg, exit_code=1, is_error=True)
            else:
                # Not a network share - do capacity check
                has_capacity, capacity_gb, checked_path = check_disk_capacity(T8_ROOT, MIN_DISK_CAPACITY_BYTES)
                if has_capacity:
                    logmsg.info("T8 drive ({path}): {capacity_gb:.2f} GB capacity ({tb:.2f} TB) - OK", path=str(checked_path), capacity_gb=capacity_gb, tb=capacity_gb / 1024)
                else:
                    # Drive too small (likely a system drive)
                    # In dry-run mode, allow it to continue with warning (no changes will be made anyway)
                    if DRY_RUN:
                        logmsg.warn("WARNING: T8 drive ({path}) capacity is too small.", path=str(checked_path))
                        logmsg.warn("Required: {min_tb:.2f} TB minimum", min_tb=min_tb)
                        logmsg.warn("Actual: {capacity_gb:.2f} GB ({tb:.2f} TB)", capacity_gb=capacity_gb, tb=capacity_gb / 1024)
                        logmsg.warn("DRY RUN: Allowing operation to continue (no changes will be made).")
                        logmsg.warn("T8 drive capacity too small ({capacity} GB) - continuing in DRY RUN mode", capacity=capacity_gb)
                    else:
                        error_msg = (
                            f"ERROR: T8 drive ({checked_path}) capacity is too small.\n"
                            f"  Required: {min_tb:.2f} TB minimum\n"
                            f"  Actual: {capacity_gb:.2f} GB ({capacity_gb / 1024:.2f} TB)\n"
                            f"This check protects system drives on the server. Exiting."
                        )
                        exit_with_error(error_msg, exit_code=1, is_error=True)
        
        logmsg.info("Disk capacity check passed.")
    except Exception as e:
        error_msg = f"ERROR: Exception during disk capacity check: {e}"
        from structured_logging import logmsg
        
        # Log to structured logging with full traceback
        logmsg.exception("Exception during disk capacity check")
        exit_with_error(error_msg)

    init_musicbrainz()

    try:
        if RESTORE_FROM_BACKUP_MODE:
            restore_flacs_from_backups(DRY_RUN)
            
            from structured_logging import logmsg
            header_key = logmsg.header("Sync backup folder", "%msg% (%count% items)")
            from sync_operations import sync_backups
            sync_backups(DRY_RUN, use_checksums=None)  # Uses T8_SYNC_USE_CHECKSUMS from config
            logmsg.header(None, key=header_key)
            
            header_key = logmsg.header("Sync master library to T8", "%msg% (%count% files copied)")
            sync_music_to_t8(DRY_RUN, use_checksums=args.t8_checksums)
            logmsg.header(None, key=header_key)
            logmsg.info("Restore mode complete.")
            logmsg.info("Refresh ROON library...")
            from roon_refresh import refresh_roon_library
            roon_refresh_success = refresh_roon_library(DRY_RUN)
            if not roon_refresh_success:
                logmsg.warn("ROON library refresh failed - you may need to manually restart ROON to see new files")
            
            from structured_logging import logmsg
            logmsg.write_summary(args.mode, DRY_RUN)
            notify_run_summary(args.mode)
            
            # Calculate exit code
            # Exit codes: 0 = clean (idle icon), 2 = warnings (yellow icon), 1 = errors (red icon)
            from structured_logging import logmsg
            
            # Count warnings/errors from structured logging system
            total_errors = logmsg.count_errors
            total_warnings = logmsg.count_warnings
            
            # Determine exit code: errors = 1 (red), warnings only = 2 (yellow), clean = 0 (green)
            # Debug: Log counts for troubleshooting
            if total_errors > 0 or total_warnings > 0:
                # Debug counts removed - already logged via logmsg if available
                pass
            
            if total_errors > 0:
                exit_code = 1
            elif total_warnings > 0:
                exit_code = 2
            else:
                exit_code = 0
            
            # Log exit status before prompt
            if exit_code == 1:
                logmsg.info("Exiting with code 1 ({total_errors} error(s)) - systray will show red error icon", total_errors=total_errors)
            elif exit_code == 2:
                logmsg.info("Exiting with code 2 ({total_warnings} warning(s)) - systray will show yellow warning icon", total_warnings=total_warnings)
            else:
                logmsg.info("Exiting with code 0 (success) - systray will show idle icon")
            
            # Keep console open for user to review
            if sys.platform == "win32":
                try:
                    logmsg.info("")
                    logmsg.info("Press Enter to close this window...")
                    input()
                except (EOFError, KeyboardInterrupt, OSError, AttributeError):
                    pass
            
            sys.exit(exit_code)

        # Step 1: Process new downloads (organize + art, no cleanup)
        import run_state
        run_state.clear()
        from structured_logging import logmsg
        # Step header processes MULTIPLE albums (each album gets its own instance)
        header_key = logmsg.header("Step 1: Process new downloads", "%msg%")
        process_downloads(DRY_RUN)
        logmsg.header(None, key=header_key)  # Close Step 1 header

        header_key = logmsg.header("Step 2: Apply UPDATE overlay", "%msg% (%count% items)", key=header_key)
        updated_album_dirs, albums_with_new_cover = apply_updates_from_overlay(DRY_RUN)
        logmsg.header(None, key=header_key)  # Close Step 2 header

        header_key = logmsg.header("Step 3: Upgrade albums to FLAC-only", "%msg% (%count% items)", key=header_key)
        upgrade_albums_to_flac_only(DRY_RUN)
        logmsg.header(None, key=header_key)  # Close Step 3 header

        header_key = logmsg.header("Step 4: Ensure cover and folder artwork", "%msg% (%count% items)", key=header_key)
        ensure_cover_and_folder_global(DRY_RUN)
        logmsg.header(None, key=header_key)  # Close Step 4 header

        header_key = logmsg.header("Step 5: Embed missing artwork", "%msg% (%count% items)", key=header_key)
        embed_missing_art_global(DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED, EMBED_IF_MISSING)
        logmsg.header(None, key=header_key)  # Close Step 5 header

        if EMBED_ALL:
            header_key = logmsg.header("Step 5.5: Embed all artwork", "%msg% (%count% items)", key=header_key)
            import os
            for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
                embed_art_into_audio_files(Path(dirpath), DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED)
            logmsg.header(None, key=header_key)  # Close Step 5.5 header

        if EMBED_FROM_UPDATES and albums_with_new_cover:
            header_key = logmsg.header("Step 5.6: Embed artwork from updates", "%msg% (%count% items)", key=header_key)
            for album_dir in sorted(albums_with_new_cover):
                embed_art_into_audio_files(album_dir, DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED)
            logmsg.header(None, key=header_key)  # Close Step 5.6 header

        header_key = logmsg.header("Step 6: Sync empty UPDATE overlay directory structure", "%msg%", key=header_key)
        sync_update_root_structure(DRY_RUN)
        logmsg.header(None, key=header_key)  # Close Step 6 header

        header_key = logmsg.header("Step 7: Ensure artist images", "%msg% (%count% artists)", key=header_key)
        from artwork import ensure_artist_images
        import os
        
        artist_dirs_processed = set()
        for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
            dir_path = Path(dirpath)
            
            # Only process directories that are direct children of MUSIC_ROOT (artist folders)
            # Skip album directories (which may have CD1/CD2 subfolders) and deeper levels
            try:
                rel_path = dir_path.relative_to(MUSIC_ROOT)
                # If path has more than 1 part, it's not an artist folder (e.g., "Artist/Album" or "Artist/Album/CD1")
                if len(rel_path.parts) != 1:
                    continue
            except ValueError:
                # Not under MUSIC_ROOT, skip
                continue
            
            # Check if this is an artist folder (has album subdirectories with audio files)
            has_albums = False
            try:
                for subdir in dir_path.iterdir():
                    if subdir.is_dir():
                        try:
                            # Check if subdir has audio files (it's an album)
                            # Look for audio files directly in the subdir (not in sub-subdirs like CD1/CD2)
                            for audio_file in subdir.iterdir():
                                if audio_file.is_file() and audio_file.suffix.lower() in {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav"}:
                                    has_albums = True
                                    break
                            if has_albums:
                                break
                        except (PermissionError, OSError) as e:
                            # Skip directories we can't access (network path issues, permissions)
                            from structured_logging import logmsg
                            logmsg.verbose("Skipping inaccessible directory: {path} ({error})", path=str(subdir), error=str(e))
                            continue
            except (PermissionError, OSError) as e:
                # Skip directories we can't access (network path issues, permissions)
                from structured_logging import logmsg
                logmsg.verbose("Skipping inaccessible directory: {path} ({error})", path=str(dir_path), error=str(e))
                continue
            
            # If this looks like an artist folder (parent of album folders), process it
            if has_albums and dir_path not in artist_dirs_processed:
                # Get artist name from folder name
                artist_name = dir_path.name
                if artist_name and artist_name != "Music":
                    ensure_artist_images(dir_path, artist_name, DRY_RUN)
                    artist_dirs_processed.add(dir_path)
        logmsg.header(None, key=header_key)  # Close Step 7 header

        header_key = logmsg.header("Step 8: Sync backup folder", "%msg% (%count% items)", key=header_key)
        from sync_operations import sync_backups
        sync_backups(DRY_RUN, use_checksums=None)  # Uses T8_SYNC_USE_CHECKSUMS from config
        logmsg.header(None, key=header_key)  # Close Step 8 header

        header_key = logmsg.header("Step 9: Sync master library to T8", "%msg% (%count% items)", key=header_key)
        sync_music_to_t8(DRY_RUN, use_checksums=args.t8_checksums)
        logmsg.header(None, key=header_key)  # Close Step 9 header

        header_key = logmsg.header("Step 10: Cleanup downloads folder", "%msg%", key=header_key)
        from file_operations import cleanup_downloads_folder
        cleanup_downloads_folder(DRY_RUN)
        logmsg.header(None, key=header_key)  # Close Step 10 header

        header_key = logmsg.header("Step 11: Refresh ROON library", "%msg%", key=header_key)
        from roon_refresh import refresh_roon_library
        roon_refresh_success = refresh_roon_library(DRY_RUN)
        if not roon_refresh_success:
            logmsg.warn("ROON library refresh failed - you may need to manually restart ROON to see new files")
        logmsg.header(None, key=header_key)  # Close Step 11 header

        # Finalization steps (detail log only, not console)
        from structured_logging import logmsg
        header_key = logmsg.header("Step 12: Writing summary log", "%msg%", verbose=True, key=None)
        try:
            # Write structured summary
            logmsg.write_summary(args.mode, DRY_RUN)
            logmsg.verbose("Summary log written successfully")
        except Exception as e:
            logmsg.error("Failed to write summary log: {error}", error=str(e))
        logmsg.header(None, key=header_key)  # Close Step 12 header

        header_key = logmsg.header("Step 13: Run summary notification", "%msg%", verbose=True, key=header_key)
        try:
            # Close Step 13 before notify so "Run complete" logs at top level (no "  > " prefix)
            logmsg.header(None, key=header_key)
            notify_run_summary(args.mode)
        except Exception as e:
            logmsg.error("Failed to send summary notification: {error}", error=str(e))
        else:
            logmsg.verbose("Summary notification sent successfully")

        # Exit with appropriate code based on warnings/errors
        # Exit codes: 0 = clean (idle icon), 2 = warnings (yellow icon), 1 = errors (red icon)
        # Calculate exit code FIRST before doing any operations that might fail
        from structured_logging import logmsg
        
        # Get counts from structured logging API
        total_errors = logmsg.count_errors
        total_warnings = logmsg.count_warnings
        
        # Determine exit code: errors = 1 (red), warnings only = 2 (yellow), clean = 0 (green)
        if total_errors > 0:
            exit_code = 1
        elif total_warnings > 0:
            exit_code = 2
        else:
            exit_code = 0
        
        # Log exit status
        if exit_code == 1:
            logmsg.info("Exiting with code 1 ({total_errors} error(s)) - systray will show red error icon", total_errors=total_errors)
        elif exit_code == 2:
            logmsg.info("Exiting with code 2 ({total_warnings} warning(s)) - systray will show yellow warning icon", total_warnings=total_warnings)
        else:
            logmsg.info("Exiting with code 0 (success) - systray will show idle icon")
        
        # IMPORTANT: Set exit code early and flush logs before user interaction
        # This ensures the correct exit code is used even if user closes the window
        # Flush all logs to ensure they're written before potential window close
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Final safety check: ensure exit_code is valid before exiting
        if exit_code not in (0, 1, 2):
            exit_code = 1
        
        # Log final exit code one more time for debugging
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Keep console open for user to review (only if running interactively)
        # On Windows, always try to wait for input when run from console
        # When run from tray launcher, this will fail gracefully and exit immediately
        # NOTE: If user closes window instead of pressing Enter, Windows may terminate
        # the process with a different exit code. The exit_code is set above to minimize
        # the window where this could happen.
        try:
            if sys.platform == "win32":
                try:
                    # Try to wait for user input - this keeps console open
                    # If stdin is not available (tray launcher), this will raise an exception
                    logmsg.info("")
                    logmsg.info("Press Enter to close this window...")
                    input()
                except (EOFError, KeyboardInterrupt, OSError, AttributeError):
                    # stdin not available or interrupted - likely running from tray launcher
                    # Just continue and exit (console will close automatically)
                    pass
        except Exception as e:
            # If anything fails here, log it but preserve the exit_code
            sys.stdout.flush()
            sys.stderr.flush()
        
        sys.exit(exit_code)

    except Exception as e:
        from structured_logging import logmsg
        
        logmsg.exception("Fatal error during run")
        
        # Write summaries (error already logged above, don't duplicate in exit_with_error)
        try:
            logmsg.write_summary(args.mode, DRY_RUN)
            notify_run_summary(args.mode)
        except Exception as summary_error:
            # If summary writing fails, log it but continue to prompt
            logmsg.exception("Error writing summary logs")
        
        # Keep console open for user to review
        if sys.platform == "win32":
            try:
                sys.stdout.flush()
                sys.stderr.flush()
                logmsg.info("")
                logmsg.info("Press Enter to close this window...")
                input()
            except (EOFError, KeyboardInterrupt, OSError, AttributeError):
                pass  # stdin not available - likely running from tray launcher
        
        sys.exit(1)


if __name__ == "__main__":
    main()

