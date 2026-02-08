"""
Sync operations: T8 sync, update overlay, and restore operations.
"""
import hashlib
import os
import shutil
from pathlib import Path
from typing import Set, Tuple

from config import AUDIO_EXT, BACKUP_ROOT, CLEAN_EMPTY_BACKUP_FOLDERS, MUSIC_ROOT, T8_ROOT, T8_SYNC_USE_CHECKSUMS, UPDATE_ROOT
from logging_utils import (
    add_album_event_label,
    album_label_from_dir,
    add_album_warning_label,
    log,
)
from structured_logging import logmsg


def remove_backup_for(rel_path: Path, dry_run: bool = False) -> None:
    """
    If a backup exists for this relative path, remove it.
    Used when a NEW original FLAC is copied from UPDATE_ROOT.
    """
    backup_path = BACKUP_ROOT / rel_path
    if backup_path.exists():
        log(f"[BACKUP] Removing obsolete backup: {backup_path}")
        if not dry_run:
            try:
                backup_path.unlink()
            except Exception as e:
                log(f"[BACKUP WARN] Could not delete backup {backup_path}: {e}")


def apply_updates_from_overlay(dry_run: bool = False) -> Tuple[Set[Path], Set[Path]]:
    """
    Copy any files found under UPDATE_ROOT into MUSIC_ROOT, mirroring structure.
    
    PRIMARY PURPOSE: Allow embedding new artwork into selected albums.
    - No way to drop a JPG by itself into downloads (without album folder) and know 
      what album on ROON to update/embed.
    - UPDATE_ROOT maintains structure synced with ROON, so you can drop cover.jpg 
      into the correct album path.
    
    SECONDARY FUNCTION: Direct overlay of music files (audio files).
    - Audio filenames are normalized using tags (same logic as downloads) to ensure consistency.
    - Files overwrite existing files (same behavior as downloads using shutil.move).
    - Later step removes MP3 if FLAC exists (FLAC-only upgrade).
    - Most music file updates come from downloads folder.
    - UPDATE_ROOT mainly used for isolated art updates.
    - Future: Frequency/sample rate comparison feature will compare sample rates for same filename/ext.
    
    Behavior:
    - Audio files: treated as new originals; any existing backup for that path is removed.
    - Other files (e.g., cover.jpg) overwrite/create assets in MUSIC_ROOT.
    - Files in UPDATE_ROOT are deleted after being applied.
    
    Returns:
      updated_album_dirs: set of album directories in MUSIC_ROOT that were touched.
      albums_with_new_cover: subset where cover.jpg came from UPDATE_ROOT.
    """
    updated_album_dirs: Set[Path] = set()
    albums_with_new_cover: Set[Path] = set()

    # Create UPDATE_ROOT if it doesn't exist
    if not UPDATE_ROOT.exists():
        if not dry_run:
            UPDATE_ROOT.mkdir(parents=True, exist_ok=True)
            log(f"[UPDATE] Created UPDATE overlay root: {UPDATE_ROOT}")
        else:
            log(f"[UPDATE] Would create UPDATE overlay root: {UPDATE_ROOT}")
        # If dry run or if folder was just created, return early (no files to process yet)
        return updated_album_dirs, albums_with_new_cover

    log(f"\n[UPDATE] Applying overlay from {UPDATE_ROOT} -> {MUSIC_ROOT}")
    
    for src in UPDATE_ROOT.rglob("*"):
        if src.is_dir():
            continue

        rel = src.relative_to(UPDATE_ROOT)
        
        # Overlay is "dumb" - preserve original filenames, no normalization
        # User manually placed the file with the exact name they want
        
        dest = MUSIC_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        # Set album context only for album-level files (2+ levels deep)
        # Artist-level files (1 level deep) are global - no album context
        current_album_key = None
        try:
            rel_parts = list(rel.parts)
            if len(rel_parts) >= 2:
                # Album-level file - set album context
                current_album_key = logmsg.begin_album(dest.parent)
            # else: Artist-level file (1 level) - keep global, no album context
        except (ValueError, OSError):
            # Can't determine, try direct path (might be album-level)
            try:
                current_album_key = logmsg.begin_album(dest.parent)
            except Exception:
                # Can't set album context - will be global
                pass
        try:
            # Set item context for this file
            item_key = logmsg.begin_item(str(src.name))
            try:
                # Initialize variables that may be used later
                should_copy = True
                upgrade_reason = []
                src_freq = None
                dest_freq = None
                
                if src.suffix.lower() in AUDIO_EXT:
                    # Overlay is "dumb" - just copy files as-is, no smart comparisons
                    # User manually placed the file, so trust their judgment
                    log(f"  [UPDATE AUDIO] {src} -> {dest}")
                    logmsg.info("COPY: %item% -> {dest}", dest=str(dest))
                    if not dry_run:
                        shutil.copy2(src, dest)
                        remove_backup_for(rel, dry_run)
                        updated_album_dirs.add(dest.parent)
                else:
                    # Handle non-audio files (artwork, etc.)
                    # Simple overlay: copy files as-is, no normalization, no conversion
                    # Overlay is "dumb" - preserves exact filename and format
                    # Step 7 (Ensure artist images) will find and convert/rename images as needed
                    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    is_image = src.suffix.lower() in image_extensions
                    
                    log(f"  [UPDATE ASSET] {src} -> {dest}")
                    logmsg.info("COPY: %item% -> {dest}", dest=str(dest))
                    if not dry_run:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        # Copy as-is - no format conversion (Step 7 will handle conversion/renaming)
                        shutil.copy2(src, dest)
                    updated_album_dirs.add(dest.parent)
                    # Track albums with new cover.jpg for embedding (Step 4 only looks for cover.jpg)
                    if is_image and dest.name.lower() == "cover.jpg":
                        albums_with_new_cover.add(dest.parent)
            finally:
                # Unset item context (common to both audio and non-audio file paths)
                logmsg.end_item(item_key)

            if not dry_run:
                try:
                    src.unlink()
                except Exception as e:
                    log(f"  [UPDATE WARN] Could not delete applied update file {src}: {e}")
                    logmsg.warn("Could not delete applied update file %item%: {error}", error=str(e))
        finally:
            # Unset album context if it was set (before next iteration or loop end)
            if current_album_key is not None:
                logmsg.end_album(current_album_key)

    # Add structured logging for updated albums (old API only - for summary log compatibility)
    for album_dir in updated_album_dirs:
        label = album_label_from_dir(album_dir)
        add_album_event_label(label, "Updated from overlay.")  # Old API only

    return updated_album_dirs, albums_with_new_cover


def sync_update_root_structure(dry_run: bool = False) -> None:
    """
    Ensure UPDATE_ROOT has the same directory structure as MUSIC_ROOT, but no files.
    Remove any directories in UPDATE_ROOT that don't exist in MUSIC_ROOT.
    """
    from structured_logging import logmsg
    
    if not UPDATE_ROOT:
        return
    
    # Create UPDATE_ROOT if it doesn't exist
    if not UPDATE_ROOT.exists():
        if not dry_run:
            UPDATE_ROOT.mkdir(parents=True, exist_ok=True)
            log(f"[UPDATE] Created UPDATE overlay root: {UPDATE_ROOT}")
        else:
            log(f"[UPDATE] Would create UPDATE overlay root: {UPDATE_ROOT}")

    log(f"\n[UPDATE] Syncing empty overlay directory structure under {UPDATE_ROOT}")

    # Create directory structure (verbose - not very interesting)
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        rel = Path(dirpath).relative_to(MUSIC_ROOT)
        upd_dir = UPDATE_ROOT / rel
        if not dry_run:
            upd_dir.mkdir(parents=True, exist_ok=True)

    # Remove obsolete directories and stray files
    for dirpath, dirnames, filenames in os.walk(UPDATE_ROOT, topdown=False):
        upd_dir = Path(dirpath)
        rel = upd_dir.relative_to(UPDATE_ROOT)
        music_dir = MUSIC_ROOT / rel

        # Set album context if this looks like an album directory
        album_key = None
        try:
            rel_parts = list(rel.parts)
            if len(rel_parts) >= 2:  # Artist/Album structure
                # Use the equivalent MUSIC_ROOT path for album context
                album_key = logmsg.begin_album(music_dir)
        except (ValueError, IndexError):
            album_key = None

        if not music_dir.exists():
            # This is an obsolete directory - determine if it's an album subdirectory or the album itself
            try:
                rel_parts = list(rel.parts)
                if len(rel_parts) >= 2:  # Artist/Album structure
                    # Extract directory name for item context
                    dir_name = upd_dir.name
                    item_key = logmsg.begin_item(dir_name)
                    if dry_run:
                        logmsg.info("Would remove obsolete overlay directory: %item%")
                    else:
                        logmsg.info("REMOVE obsolete overlay directory: %item%")
                    log(f"  [UPDATE] Removing obsolete overlay dir: {upd_dir}")
                    logmsg.end_item(item_key)
                else:
                    # Not an album directory, log without item context
                    if dry_run:
                        logmsg.info("Would remove obsolete overlay directory: {path}", path=str(upd_dir))
                    else:
                        logmsg.info("REMOVE obsolete overlay directory: {path}", path=str(upd_dir))
                    log(f"  [UPDATE] Removing obsolete overlay dir: {upd_dir}")
            except (ValueError, IndexError):
                # Can't parse path, log without context
                if dry_run:
                    logmsg.info("Would remove obsolete overlay directory: {path}", path=str(upd_dir))
                else:
                    logmsg.info("REMOVE obsolete overlay directory: {path}", path=str(upd_dir))
                log(f"  [UPDATE] Removing obsolete overlay dir: {upd_dir}")
            
            if not dry_run:
                try:
                    upd_dir.rmdir()
                except OSError:
                    pass
        else:
            if not dry_run:
                for f in list(upd_dir.iterdir()):
                    if f.is_file():
                        item_key = logmsg.begin_item(f.name)
                        logmsg.info("REMOVE stray file from overlay: %item%")
                        log(f"  [UPDATE] Removing stray file from overlay: {f}")
                        try:
                            f.unlink()
                        except OSError:
                            pass
                        logmsg.end_item(item_key)
        
        if album_key:
            logmsg.end_album(album_key)


def sync_music_to_t8(dry_run: bool = False, use_checksums: bool = None) -> None:
    """
    Simple mirror: Copy everything from ROON to T8.
    T8 is just a straight mirror of ROON - no complex logic needed.
    
    Args:
        dry_run: If True, don't make any changes
        use_checksums: If True, use MD5 checksums for comparison (slower but accurate).
                       If None, uses T8_SYNC_USE_CHECKSUMS from config.
                       If False, uses fast size+mtime comparison (default).
    """
    from structured_logging import logmsg
    
    if use_checksums is None:
        use_checksums = T8_SYNC_USE_CHECKSUMS

    if T8_ROOT is None:
        log("\n[T8 SYNC] T8_ROOT is None, skipping sync.")
        return

    log(f"\n[T8 SYNC] Mirroring {MUSIC_ROOT} -> {T8_ROOT}")
    
    # Header is already set by main.py (Step 5), so we don't set it here

    # Copy all files from ROON to T8
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        src_dir = Path(dirpath)
        rel = src_dir.relative_to(MUSIC_ROOT)
        dst_dir = T8_ROOT / rel
        if not dry_run:
            dst_dir.mkdir(parents=True, exist_ok=True)

        # Set album context if this looks like an album directory
        try:
            rel_parts = list(rel.parts)
            if len(rel_parts) >= 2:  # Artist/Album structure
                album_key = logmsg.begin_album(src_dir)
            else:
                album_key = None
        except (ValueError, IndexError):
            album_key = None
        
        # For artist-level files, get artist name for context in log messages
        artist_name = None
        if album_key is None and len(rel.parts) == 1:
            artist_name = rel.parts[0]

        for name in filenames:
            src_file = src_dir / name
            dst_file = dst_dir / name
            item_key = logmsg.begin_item(name)
            
            try:
                # Check if file needs to be copied
                should_copy = False
                skip_reason = None
                
                if not dst_file.exists():
                    should_copy = True
                else:
                    # Destination exists - use fast comparison: size + mtime
                    # This is much faster than checksums while still being reliable
                    try:
                        src_stat = src_file.stat()
                        dst_stat = dst_file.stat()
                        src_size = src_stat.st_size
                        dst_size = dst_stat.st_size
                        src_mtime = src_stat.st_mtime
                        dst_mtime = dst_stat.st_mtime
                        
                        # Quick check: if sizes differ, files are definitely different
                        if src_size != dst_size:
                            should_copy = True
                        else:
                            # Same size - use configured comparison method
                            if use_checksums:
                                # Accurate mode: compute checksums (slower but more reliable)
                                def file_checksum(path: Path) -> str:
                                    """Calculate MD5 checksum of a file."""
                                    hash_md5 = hashlib.md5()
                                    try:
                                        with path.open("rb") as f:
                                            for chunk in iter(lambda: f.read(4096), b""):
                                                hash_md5.update(chunk)
                                        return hash_md5.hexdigest()
                                    except Exception:
                                        return None
                                
                                src_checksum = file_checksum(src_file)
                                dst_checksum = file_checksum(dst_file)
                                
                                if src_checksum is None or dst_checksum is None:
                                    # Couldn't compute checksum - copy to be safe
                                    logmsg.warn("Could not compute checksum for %item%, will copy")
                                    log(f"  [T8 SYNC WARN] Could not compute checksum for {src_file.name}, will copy")
                                    should_copy = True
                                elif src_checksum == dst_checksum:
                                    # Files are identical - skip
                                    skip_reason = "files are identical (same checksum)"
                                else:
                                    # Files are different - copy
                                    should_copy = True
                            else:
                                # Fast mode: compare mtimes (much faster)
                                # If mtimes match (within 1 second tolerance for network filesystem rounding), likely same file
                                # If source is newer, copy (file was updated)
                                # If destination is newer, could be same file or different file with newer timestamp
                                # For safety: only skip if sizes match AND mtimes are very close (within 1 second)
                                mtime_diff = abs(src_mtime - dst_mtime)
                                if mtime_diff <= 1.0:
                                    # Sizes match and mtimes are very close - likely the same file
                                    skip_reason = f"files appear identical (size: {src_size} bytes, mtime diff: {mtime_diff:.1f}s)"
                                else:
                                    # Sizes match but mtimes differ significantly - copy to be safe
                                    should_copy = True
                    except OSError as e:
                        # If we can't stat/read files, try to copy anyway
                        logmsg.warn("Could not check destination %item%: {error}, will attempt copy", error=str(e))
                        log(f"  [T8 SYNC WARN] Could not check destination {dst_file}: {e}, will attempt copy")
                        should_copy = True
                
                if should_copy:
                    if dry_run:
                        if artist_name:
                            logmsg.info("Would copy {artist}: %item% to T8", artist=artist_name)
                        else:
                            logmsg.info("Would copy %item% to T8")
                    else:
                        if artist_name:
                            logmsg.info("COPY: {artist}: %item% to T8", artist=artist_name)
                        else:
                            logmsg.info("COPY: %item% to T8")
                    log(f"  COPY: {src_file} -> {dst_file}")
                    if not dry_run:
                        try:
                            shutil.copy2(src_file, dst_file)
                        except Exception as e:
                            if artist_name:
                                logmsg.warn("Failed to copy {artist}: %item% to T8: {error}", artist=artist_name, error=str(e))
                            else:
                                logmsg.warn("Failed to copy %item% to T8: {error}", error=str(e))
                            log(f"    [T8 SYNC ERROR] Failed to copy {src_file.name}: {e}")
                            add_album_warning_label(album_label_from_dir(src_dir), f"[WARN] Failed to copy {src_file.name} to T8: {e}")
                elif skip_reason:
                    if artist_name:
                        logmsg.verbose("SKIP: {artist}: %item% ({reason})", artist=artist_name, reason=skip_reason)
                    else:
                        logmsg.verbose("SKIP: %item% ({reason})", reason=skip_reason)
                    log(f"  SKIP: {src_file.name} ({skip_reason})")
            except Exception as e:
                if artist_name:
                    logmsg.warn("Error processing {artist}: %item%: {error}", artist=artist_name, error=str(e))
                else:
                    logmsg.warn("Error processing %item%: {error}", error=str(e))
                log(f"  [T8 SYNC ERROR] Error processing {src_file.name}: {e}")
                add_album_warning_label(album_label_from_dir(src_dir), f"[WARN] Error syncing {src_file.name} to T8: {e}")
            finally:
                logmsg.end_item(item_key)
        
        if album_key:
            logmsg.end_album(album_key)
    
    # Header is managed by main.py, so we don't close it here

    # Remove files on T8 that don't exist in ROON
    # (This is a separate cleanup phase, not part of the main sync count)
    for dirpath, dirnames, filenames in os.walk(T8_ROOT, topdown=False):
        dst_dir = Path(dirpath)
        rel = dst_dir.relative_to(T8_ROOT)
        src_dir = MUSIC_ROOT / rel

        # Set album context if this looks like an album directory
        try:
            rel_parts = list(rel.parts)
            if len(rel_parts) >= 2:  # Artist/Album structure
                album_key = logmsg.begin_album(src_dir)
            else:
                album_key = None
        except (ValueError, IndexError):
            album_key = None

        for name in filenames:
            dst_file = dst_dir / name
            src_file = src_dir / name
            if not src_file.exists():
                item_key = logmsg.begin_item(name)
                if dry_run:
                    logmsg.info("Would delete %item% from T8 (no source)")
                else:
                    logmsg.info("DELETE: %item% from T8 (no source)")
                log(f"  DELETE on T8 (no source): {dst_file}")
                if not dry_run:
                    try:
                        dst_file.unlink()
                    except OSError as e:
                        logmsg.warn("Could not delete %item% from T8: {error}", error=str(e))
                        log(f"    [WARN] Could not delete {dst_file}: {e}")
                logmsg.end_item(item_key)

        # Remove empty directories
        if not os.listdir(dst_dir):
            if dry_run:
                logmsg.verbose("Would remove empty directory on T8: {path}", path=str(dst_dir))
            else:
                logmsg.verbose("REMOVE empty dir on T8: {path}", path=str(dst_dir))
            log(f"  REMOVE empty dir on T8: {dst_dir}")
            if not dry_run:
                try:
                    dst_dir.rmdir()
                except OSError:
                    pass
        
        if album_key:
            logmsg.end_album(album_key)


def sync_backups(dry_run: bool = False, use_checksums: bool = None) -> None:
    """
    Sync backup folder with live files:
    - If backup and live file exist and are identical: remove backup
    - If backup exists and live file is different: keep backup (files differ, backup may be needed)
    - If backup exists but live file doesn't: remove orphan backup
    - Clean up empty folders including backup root
    
    Uses fast comparison (size + mtime) by default, or checksums if use_checksums=True.
    """
    from config import BACKUP_ROOT, MUSIC_ROOT, CLEAN_EMPTY_BACKUP_FOLDERS, T8_SYNC_USE_CHECKSUMS
    from logging_utils import log, album_label_from_dir, add_album_event_label, add_album_warning_label
    from structured_logging import logmsg
    
    if use_checksums is None:
        use_checksums = T8_SYNC_USE_CHECKSUMS
    
    log(f"\n[SYNC BACKUP] Syncing backup folder with live files...")
    if not BACKUP_ROOT.exists():
        log("  No backup root found; nothing to sync.")
        return
    
    def file_checksum(path: Path) -> str:
        """Calculate MD5 checksum of a file."""
        hash_md5 = hashlib.md5()
        try:
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            log(f"    [WARN] Could not calculate checksum for {path}: {e}")
            return None
    
    backups_processed = 0
    backups_removed = 0
    backups_restored = 0
    
    # Walk backup directory (topdown=False to process deepest first)
    # Handle permission errors during directory walk
    try:
        backup_walk = os.walk(BACKUP_ROOT, topdown=False)
    except (OSError, PermissionError) as e:
        logmsg.error("Could not access backup root directory: {error}", error=str(e))
        log(f"  [SYNC BACKUP ERROR] Could not access backup root: {e}")
        return
    
    for dirpath, dirnames, filenames in backup_walk:
        backup_dir = Path(dirpath)
        
        # Skip directories we can't access
        try:
            # Test if we can access this directory
            _ = list(backup_dir.iterdir())
        except (OSError, PermissionError) as e:
            logmsg.verbose("Skipping inaccessible backup directory: {path} ({error})", path=str(backup_dir), error=str(e))
            continue
        
        # Set album context if this looks like an album directory
        try:
            rel_backup = backup_dir.relative_to(BACKUP_ROOT)
            rel_parts = list(rel_backup.parts)
            if len(rel_parts) >= 2:  # Artist/Album structure
                # Find corresponding live directory for album context
                live_dir = MUSIC_ROOT / rel_backup
                if live_dir.exists():
                    album_key = logmsg.begin_album(live_dir)
                else:
                    # Live directory doesn't exist (orphan backup), but we can still set album context
                    # Construct the equivalent MUSIC_ROOT path for proper album context parsing
                    # This ensures begin_album() can correctly extract artist/album/year
                    equivalent_music_path = MUSIC_ROOT / rel_backup
                    album_key = logmsg.begin_album(equivalent_music_path)
            else:
                album_key = None
        except (ValueError, IndexError):
            album_key = None
        
        for name in filenames:
            backup_file = backup_dir / name
            backups_processed += 1
            item_key = logmsg.begin_item(name)
            
            # Get relative path from backup root
            try:
                rel = backup_file.relative_to(BACKUP_ROOT)
            except ValueError:
                logmsg.end_item(item_key)
                continue
            
            # Find corresponding live file
            live_file = MUSIC_ROOT / rel
            
            if live_file.exists():
                # Both backup and live file exist - compare them
                files_identical = False
                
                if use_checksums:
                    # Use checksums (slower but accurate)
                    backup_checksum = file_checksum(backup_file)
                    live_checksum = file_checksum(live_file)
                    
                    if backup_checksum and live_checksum:
                        files_identical = (backup_checksum == live_checksum)
                    else:
                        # Couldn't calculate checksums - assume different to be safe
                        files_identical = False
                else:
                    # Fast comparison: size + mtime (much faster)
                    try:
                        backup_stat = backup_file.stat()
                        live_stat = live_file.stat()
                        files_identical = (
                            backup_stat.st_size == live_stat.st_size and
                            abs(backup_stat.st_mtime - live_stat.st_mtime) < 1.0  # Within 1 second
                        )
                    except (OSError, FileNotFoundError):
                        # Can't stat files - assume different to be safe
                        files_identical = False
                
                if files_identical:
                    # Files are identical - remove backup (no need to keep it)
                    if dry_run:
                        logmsg.info("Would remove backup %item% (files identical)")
                    else:
                        logmsg.info("REMOVE backup %item% (files identical)")
                    log(f"  [SYNC BACKUP] Files identical, removing backup: {backup_file.name}")
                    if not dry_run:
                        try:
                            backup_file.unlink()
                            backups_removed += 1
                        except Exception as e:
                            logmsg.warn("Could not remove backup %item%: {error}", error=str(e))
                            log(f"    [WARN] Could not remove backup {backup_file}: {e}")
                else:
                    # Files are different - keep backup (files differ, backup may be needed)
                    logmsg.verbose("KEEP backup %item% (files differ)")
                    log(f"  [SYNC BACKUP] Files differ, keeping backup: {backup_file.name}")
            else:
                # Backup exists but live file doesn't - delete orphan backup
                # If file was deleted from Music, backup is orphaned and shouldn't overwrite potential new original
                if dry_run:
                    logmsg.info("Would remove orphan backup %item% (live file missing)")
                else:
                    logmsg.info("REMOVE orphan backup %item% (live file missing)")
                log(f"  [SYNC BACKUP] Live file missing, removing orphan backup: {backup_file.name}")
                if not dry_run:
                    try:
                        backup_file.unlink()
                        backups_removed += 1
                    except Exception as e:
                        logmsg.warn("Could not delete orphan backup %item%: {error}", error=str(e))
                        log(f"    [WARN] Could not delete orphan backup {backup_file}: {e}")
                        label = album_label_from_dir(backup_file.parent)
                        add_album_warning_label(label, f"[WARN] Could not delete orphan backup {backup_file.name}: {e}")
            
            logmsg.end_item(item_key)
        
        if album_key:
            logmsg.end_album(album_key)
        
        # Clean up empty directories after processing files
        if not dry_run and CLEAN_EMPTY_BACKUP_FOLDERS:
            current = backup_dir
            while True:
                try:
                    # Stop at BACKUP_ROOT (we'll check it separately)
                    if current.resolve() == BACKUP_ROOT.resolve():
                        break
                except FileNotFoundError:
                    break

                try:
                    contents = list(current.iterdir())
                except (FileNotFoundError, OSError, PermissionError):
                    # Can't access directory - skip cleanup for this branch
                    break

                if contents:
                    # Directory not empty, stop cleaning
                    break

                logmsg.verbose("Removing empty backup folder: {path}", path=str(current))
                log(f"  [CLEANUP] Removing empty backup folder: {current}")
                try:
                    current.rmdir()
                except OSError as e:
                    logmsg.warn("Could not remove empty backup folder {path}: {error}", path=str(current), error=str(e))
                    log(f"    [CLEANUP WARN] Could not remove {current}: {e}")
                    break

                # Move up to parent directory
                current = current.parent
    
    # Note: Empty backup root cleanup is handled in startup permission checks
    # No need to handle it here - if it's empty, it was already removed at startup
    
    log(f"[SYNC BACKUP] Processed {backups_processed} backups: {backups_removed} removed, {backups_restored} restored")


def restore_flacs_from_backups(dry_run: bool = False) -> None:
    """
    Restore FLACs from BACKUP_ROOT into MUSIC_ROOT and delete backups.
    Only affects files that have backup copies.
    """
    from structured_logging import logmsg
    from config import BACKUP_ROOT, MUSIC_ROOT, CLEAN_EMPTY_BACKUP_FOLDERS
    
    restore_key = logmsg.header("Restore from backups", "%msg% (%count% files restored)")
    
    if not BACKUP_ROOT.exists():
        logmsg.info("No backup root found; nothing to restore.")
        log("  No backup root found; nothing to restore.")
        logmsg.header(None, key=restore_key)
        return

    log(f"\n[RESTORE] Restoring FLACs from backup under {BACKUP_ROOT}")
    
    # Track current album for context
    current_album_dir = None
    current_album_key = None
    
    for dirpath, dirnames, filenames in os.walk(BACKUP_ROOT, topdown=False):
        backup_dir = Path(dirpath)
        for name in filenames:
            backup_file = backup_dir / name
            if backup_file.suffix.lower() != ".flac":
                continue
            rel = backup_file.relative_to(BACKUP_ROOT)
            dest = MUSIC_ROOT / rel
            
            # Set album context if album changed
            if current_album_dir != dest.parent:
                if current_album_key is not None:
                    logmsg.end_album(current_album_key)
                current_album_dir = dest.parent
                current_album_key = logmsg.begin_album(current_album_dir)
            
            # Set item context
            item_key = logmsg.begin_item(dest.name)
            try:
                # Check if files are identical (skip if so)
                if dest.exists():
                    try:
                        backup_size = backup_file.stat().st_size
                        dest_size = dest.stat().st_size
                        backup_mtime = backup_file.stat().st_mtime
                        dest_mtime = dest.stat().st_mtime
                        if backup_size == dest_size and abs(backup_mtime - dest_mtime) < 2.0:
                            logmsg.verbose("SKIP: %item% (files appear identical (size: {size} bytes, mtime diff: {diff}s))", size=backup_size, diff=abs(backup_mtime - dest_mtime))
                            log(f"  SKIP: {dest.name} (files appear identical (size: {backup_size} bytes, mtime diff: {abs(backup_mtime - dest_mtime):.1f}s))")
                            continue
                    except (OSError, FileNotFoundError):
                        pass  # Can't compare, proceed with restore
                
                logmsg.info("RESTORE: %item%")
                log(f"  RESTORE: {backup_file} -> {dest}")
                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_file, dest)
                    try:
                        backup_file.unlink()
                    except OSError as e:
                        logmsg.warn("Could not delete backup %item%: {error}", error=str(e))
                        log(f"    [WARN] Could not delete backup {backup_file}: {e}")
                        label = album_label_from_dir(dest.parent)
                        add_album_warning_label(label, f"[WARN] Could not delete backup {backup_file}: {e}")
            finally:
                logmsg.end_item(item_key)

        # Clean up empty directories after restoring files
        if not dry_run and CLEAN_EMPTY_BACKUP_FOLDERS:
            current = backup_dir
            while True:
                try:
                    # Stop at BACKUP_ROOT (don't delete the root itself)
                    if current.resolve() == BACKUP_ROOT.resolve():
                        break
                except FileNotFoundError:
                    break

                try:
                    contents = list(current.iterdir())
                except (FileNotFoundError, OSError, PermissionError):
                    # Can't access directory - skip cleanup for this branch
                    break

                if contents:
                    # Directory not empty, stop cleaning
                    break

                try:
                    rel_path = current.relative_to(BACKUP_ROOT)
                    item_id = str(rel_path)
                except ValueError:
                    item_id = current.name
                
                item_key = logmsg.begin_item(item_id)
                try:
                    logmsg.verbose("Removing empty backup folder: %item%")
                    log(f"  [CLEANUP] Removing empty backup folder: {current}")
                    current.rmdir()
                except OSError as e:
                    logmsg.warn("Could not remove empty backup folder %item%: {error}", error=str(e))
                    log(f"    [CLEANUP WARN] Could not remove {current}: {e}")
                    logmsg.end_item(item_key)
                    break
                finally:
                    logmsg.end_item(item_key)

                # Move up to parent directory
                current = current.parent
    
    # End album context if set
    if current_album_key is not None:
        logmsg.end_album(current_album_key)

    # After all restores, check if BACKUP_ROOT itself is empty and delete it
    if not dry_run and CLEAN_EMPTY_BACKUP_FOLDERS:
        try:
            if BACKUP_ROOT.exists():
                contents = list(BACKUP_ROOT.iterdir())
                if not contents:
                    logmsg.verbose("Backup root is empty, removing it (will be recreated on next backup)")
                    log(f"  [CLEANUP] Backup root is empty, removing it (will be recreated on next backup)")
                    try:
                        BACKUP_ROOT.rmdir()
                    except OSError as e:
                        logmsg.warn("Could not remove empty backup root: {error}", error=str(e))
                        log(f"  [CLEANUP WARN] Could not remove empty backup root {BACKUP_ROOT}: {e}")
        except Exception as e:
            logmsg.warn("Could not check backup root: {error}", error=str(e))
            log(f"  [CLEANUP WARN] Could not check backup root: {e}")
    
    # Close restore header
    logmsg.header(None, key=restore_key)

