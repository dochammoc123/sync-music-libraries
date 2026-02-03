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
        
        # Set album context unconditionally (will unset at end of iteration, so always ready to set here)
        current_album_key = logmsg.set_album(dest.parent)
        try:
            # Set item context for this file
            item_key = logmsg.set_item(str(src.name))
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
                    # Simple overlay: copy files as-is, no normalization
                    # If you want embedding to work, name it cover.jpg yourself
                    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    is_image = src.suffix.lower() in image_extensions
                    
                    log(f"  [UPDATE ASSET] {src} -> {dest}")
                    logmsg.info("COPY: %item% -> {dest}", dest=str(dest))
                    if not dry_run:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        # Convert image format if needed (PNG/GIF → JPG)
                        if is_image and src.suffix.lower() in {".png", ".gif", ".webp"}:
                            try:
                                from PIL import Image
                                with Image.open(src) as img:
                                    # Convert RGBA to RGB if needed
                                    if img.mode in ("RGBA", "LA", "P"):
                                        rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                                        if img.mode == "P":
                                            img = img.convert("RGBA")
                                        rgb_img.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                                        img = rgb_img
                                    img.save(dest, "JPEG", quality=95, optimize=True)
                                logmsg.verbose("Converted %item% to JPEG (optimized)")
                            except Exception as e:
                                log(f"  [UPDATE WARN] Could not convert {src.name} to JPEG, copying as-is: {e}")
                                logmsg.warn("Could not convert %item% to JPEG, copying as-is: {error}", error=str(e))
                                shutil.copy2(src, dest)
                        else:
                            shutil.copy2(src, dest)
                    updated_album_dirs.add(dest.parent)
                    # Track albums with new cover.jpg for embedding (Step 4 only looks for cover.jpg)
                    if is_image and dest.name.lower() == "cover.jpg":
                        albums_with_new_cover.add(dest.parent)
            finally:
                # Unset item context (common to both audio and non-audio file paths)
                logmsg.unset_item(item_key)

            if not dry_run:
                try:
                    src.unlink()
                except Exception as e:
                    log(f"  [UPDATE WARN] Could not delete applied update file {src}: {e}")
                    logmsg.warn("Could not delete applied update file %item%: {error}", error=str(e))
        finally:
            # Unset album context unconditionally (before next iteration or loop end)
            logmsg.unset_album(current_album_key)

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

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        rel = Path(dirpath).relative_to(MUSIC_ROOT)
        upd_dir = UPDATE_ROOT / rel
        if not dry_run:
            upd_dir.mkdir(parents=True, exist_ok=True)

    for dirpath, dirnames, filenames in os.walk(UPDATE_ROOT, topdown=False):
        upd_dir = Path(dirpath)
        rel = upd_dir.relative_to(UPDATE_ROOT)
        music_dir = MUSIC_ROOT / rel

        if not music_dir.exists():
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
                        log(f"  [UPDATE] Removing stray file from overlay: {f}")
                        try:
                            f.unlink()
                        except OSError:
                            pass


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
    if use_checksums is None:
        use_checksums = T8_SYNC_USE_CHECKSUMS
    
    if T8_ROOT is None:
        log("\n[T8 SYNC] T8_ROOT is None, skipping sync.")
        return

    log(f"\n[T8 SYNC] Mirroring {MUSIC_ROOT} -> {T8_ROOT}")

    # Copy all files from ROON to T8
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        src_dir = Path(dirpath)
        rel = src_dir.relative_to(MUSIC_ROOT)
        dst_dir = T8_ROOT / rel
        if not dry_run:
            dst_dir.mkdir(parents=True, exist_ok=True)

        for name in filenames:
            src_file = src_dir / name
            dst_file = dst_dir / name
            
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
                        log(f"  [T8 SYNC WARN] Could not check destination {dst_file}: {e}, will attempt copy")
                        should_copy = True
                
                if should_copy:
                    log(f"  COPY: {src_file} -> {dst_file}")
                    if not dry_run:
                        try:
                            shutil.copy2(src_file, dst_file)
                        except Exception as e:
                            log(f"    [T8 SYNC ERROR] Failed to copy {src_file.name}: {e}")
                            add_album_warning_label(album_label_from_dir(src_dir), f"[WARN] Failed to copy {src_file.name} to T8: {e}")
                elif skip_reason:
                    log(f"  SKIP: {src_file.name} ({skip_reason})")
            except Exception as e:
                log(f"  [T8 SYNC ERROR] Error processing {src_file.name}: {e}")
                add_album_warning_label(album_label_from_dir(src_dir), f"[WARN] Error syncing {src_file.name} to T8: {e}")

    # Remove files on T8 that don't exist in ROON
    for dirpath, dirnames, filenames in os.walk(T8_ROOT, topdown=False):
        dst_dir = Path(dirpath)
        rel = dst_dir.relative_to(T8_ROOT)
        src_dir = MUSIC_ROOT / rel

        for name in filenames:
            dst_file = dst_dir / name
            src_file = src_dir / name
            if not src_file.exists():
                log(f"  DELETE on T8 (no source): {dst_file}")
                if not dry_run:
                    try:
                        dst_file.unlink()
                    except OSError as e:
                        log(f"    [WARN] Could not delete {dst_file}: {e}")

        # Remove empty directories
        if not os.listdir(dst_dir):
            log(f"  REMOVE empty dir on T8: {dst_dir}")
            if not dry_run:
                try:
                    dst_dir.rmdir()
                except OSError:
                    pass


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
    for dirpath, dirnames, filenames in os.walk(BACKUP_ROOT, topdown=False):
        backup_dir = Path(dirpath)
        for name in filenames:
            backup_file = backup_dir / name
            backups_processed += 1
            
            # Get relative path from backup root
            try:
                rel = backup_file.relative_to(BACKUP_ROOT)
            except ValueError:
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
                    log(f"  [SYNC BACKUP] Files identical, removing backup: {backup_file.name}")
                    if not dry_run:
                        try:
                            backup_file.unlink()
                            backups_removed += 1
                        except Exception as e:
                            log(f"    [WARN] Could not remove backup {backup_file}: {e}")
                else:
                    # Files are different - keep backup (files differ, backup may be needed)
                    log(f"  [SYNC BACKUP] Files differ, keeping backup: {backup_file.name}")
            else:
                # Backup exists but live file doesn't - delete orphan backup
                # If file was deleted from Music, backup is orphaned and shouldn't overwrite potential new original
                log(f"  [SYNC BACKUP] Live file missing, removing orphan backup: {backup_file.name}")
                if not dry_run:
                    try:
                        backup_file.unlink()
                        backups_removed += 1
                    except Exception as e:
                        log(f"    [WARN] Could not delete orphan backup {backup_file}: {e}")
                        label = album_label_from_dir(backup_file.parent)
                        add_album_warning_label(label, f"[WARN] Could not delete orphan backup {backup_file.name}: {e}")
        
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
                except FileNotFoundError:
                    break

                if contents:
                    # Directory not empty, stop cleaning
                    break

                log(f"  [CLEANUP] Removing empty backup folder: {current}")
                try:
                    current.rmdir()
                except OSError as e:
                    log(f"    [CLEANUP WARN] Could not remove {current}: {e}")
                    break

                # Move up to parent directory
                current = current.parent
    
    # After all processing, check if BACKUP_ROOT itself is empty and delete it
    if not dry_run and CLEAN_EMPTY_BACKUP_FOLDERS:
        if BACKUP_ROOT.exists():
            try:
                contents = list(BACKUP_ROOT.iterdir())
                if not contents:
                    log(f"  [CLEANUP] Removing empty backup root: {BACKUP_ROOT}")
                    BACKUP_ROOT.rmdir()
            except Exception as e:
                log(f"  [CLEANUP WARN] Could not remove empty backup root {BACKUP_ROOT}: {e}")
    
    log(f"[SYNC BACKUP] Processed {backups_processed} backups: {backups_removed} removed, {backups_restored} restored")


def restore_flacs_from_backups(dry_run: bool = False) -> None:
    """
    Restore FLACs from BACKUP_ROOT into MUSIC_ROOT and delete backups.
    Only affects files that have backup copies.
    """
    log(f"\n[RESTORE] Restoring FLACs from backup under {BACKUP_ROOT}")
    if not BACKUP_ROOT.exists():
        log("  No backup root found; nothing to restore.")
        return

    for dirpath, dirnames, filenames in os.walk(BACKUP_ROOT, topdown=False):
        backup_dir = Path(dirpath)
        for name in filenames:
            backup_file = backup_dir / name
            if backup_file.suffix.lower() != ".flac":
                continue
            rel = backup_file.relative_to(BACKUP_ROOT)
            dest = MUSIC_ROOT / rel
            log(f"  RESTORE: {backup_file} -> {dest}")
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, dest)
                try:
                    backup_file.unlink()
                except OSError as e:
                    log(f"    [WARN] Could not delete backup {backup_file}: {e}")
                    label = album_label_from_dir(dest.parent)
                    add_album_warning_label(label, f"[WARN] Could not delete backup {backup_file}: {e}")

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
                except FileNotFoundError:
                    break

                if contents:
                    # Directory not empty, stop cleaning
                    break

                log(f"  [CLEANUP] Removing empty backup folder: {current}")
                try:
                    current.rmdir()
                except OSError as e:
                    log(f"    [CLEANUP WARN] Could not remove {current}: {e}")
                    break

                # Move up to parent directory
                current = current.parent

    # After all restores, check if BACKUP_ROOT itself is empty and delete it
    if not dry_run and CLEAN_EMPTY_BACKUP_FOLDERS:
        try:
            if BACKUP_ROOT.exists():
                contents = list(BACKUP_ROOT.iterdir())
                if not contents:
                    log(f"  [CLEANUP] Backup root is empty, removing it (will be recreated on next backup)")
                    try:
                        BACKUP_ROOT.rmdir()
                    except OSError as e:
                        log(f"  [CLEANUP WARN] Could not remove empty backup root {BACKUP_ROOT}: {e}")
        except Exception as e:
            log(f"  [CLEANUP WARN] Could not check backup root: {e}")

