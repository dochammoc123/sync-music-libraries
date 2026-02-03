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

    if not UPDATE_ROOT.exists():
        return updated_album_dirs, albums_with_new_cover

    log(f"\n[UPDATE] Applying overlay from {UPDATE_ROOT} -> {MUSIC_ROOT}")
    
    for src in UPDATE_ROOT.rglob("*"):
        if src.is_dir():
            continue

        rel = src.relative_to(UPDATE_ROOT)
        
        # Normalize audio filenames using tags (same logic as downloads)
        if src.suffix.lower() in AUDIO_EXT:
            ext = src.suffix
            # Try to read tags from source file for filename generation (same as downloads)
            tags_to_use = None
            try:
                from tag_operations import get_tags
                original_tags = get_tags(src)
                if original_tags and original_tags.get("title") and original_tags.get("tracknum", 0) > 0:
                    # File has good tags, use them for filename (same as downloads)
                    tags_to_use = original_tags.copy()
            except Exception:
                # Can't read tags, will use original filename (same as downloads fallback)
                pass
            
            if tags_to_use:
                # Generate filename from tags (same as downloads)
                from tag_operations import format_track_filename
                normalized_filename = format_track_filename(tags_to_use, ext)
                # Update rel to use normalized filename
                rel = rel.parent / normalized_filename
                log(f"  [UPDATE AUDIO] Normalized filename: {src.name} -> {normalized_filename}")
                logmsg.verbose("Normalized filename: %item% -> {normalized}", normalized=normalized_filename)
            else:
                # No tags or incomplete tags, use original filename (same as downloads fallback)
                log(f"  [UPDATE AUDIO] No tags found, using original filename: {src.name}")
                logmsg.verbose("No tags found, using original filename: %item%")
        
        dest = MUSIC_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        # Set album context unconditionally (will unset at end of iteration, so always ready to set here)
        current_album_key = logmsg.set_album(dest.parent)
        try:
            # Set item context for this file
            item_key = logmsg.set_item(str(src.name))
            try:
                if src.suffix.lower() in AUDIO_EXT:
                    # Check if destination exists - compare frequency first, then file size
                    # Handles partial/corrupted files and frequency upgrades
                    # We also check for suspiciously small file sizes (heuristic warning)
                    should_copy = True
                    
                    # Check source file for size warnings (may indicate truncation)
                    from tag_operations import check_file_size_warning
                    size_warning = check_file_size_warning(src)
                    if size_warning:
                        level, message = size_warning
                        log(f"  [UPDATE {level}] {src.name}: {message}")
                        if level == "WARN":
                            logmsg.warn("%item%: {warning_msg}", warning_msg=message)
                        elif level == "ERROR":
                            logmsg.error("%item%: {error_msg}", error_msg=message)
                    
                    if dest.exists():
                        from tag_operations import get_sample_rate
                        
                        src_size = src.stat().st_size
                        dest_size = dest.stat().st_size
                        src_freq = get_sample_rate(src)
                        dest_freq = get_sample_rate(dest)
                        
                        # Compare: frequency first, then file size
                        upgrade_reason = []
                        if src_freq and dest_freq:
                            if src_freq > dest_freq:
                                upgrade_reason.append(f"frequency: {src_freq}Hz > {dest_freq}Hz")
                        should_copy = True
                    elif src_freq < dest_freq:
                        should_copy = False
                        log(f"  [UPDATE SKIP] {dest.name} (existing has higher frequency: {dest_freq}Hz > {src_freq}Hz)")
                        logmsg.info("SKIP: %item% (existing has higher frequency: {dest_freq}Hz > {src_freq}Hz)", dest_freq=dest_freq, src_freq=src_freq)
                    else:
                        # Same frequency, compare file size
                        if src_size > dest_size:
                            upgrade_reason.append(f"size: {src_size} > {dest_size} bytes")
                            should_copy = True
                        else:
                            should_copy = False
                            log(f"  [UPDATE SKIP] {dest.name} (same frequency {src_freq}Hz, existing file is larger or equal: {dest_size} >= {src_size} bytes)")
                            logmsg.info("SKIP: %item% (same frequency {freq}Hz, existing file is larger or equal: {dest_size} >= {src_size} bytes)", freq=src_freq, dest_size=dest_size, src_size=src_size)
                else:
                    # Can't determine frequency, fall back to file size only
                    if src_size > dest_size:
                        upgrade_reason.append(f"size: {src_size} > {dest_size} bytes")
                        should_copy = True
                    else:
                        should_copy = False
                        log(f"  [UPDATE SKIP] {dest.name} (existing file is larger or equal: {dest_size} >= {src_size} bytes)")
                        logmsg.info("SKIP: %item% (existing file is larger or equal: {dest_size} >= {src_size} bytes)", dest_size=dest_size, src_size=src_size)
                
                if should_copy and upgrade_reason:
                    freq_str = f" ({src_freq}Hz vs {dest_freq}Hz)" if src_freq and dest_freq else ""
                    log(f"  [UPDATE UPGRADE] {dest.name}{freq_str} - {', '.join(upgrade_reason)}")
                    logmsg.info("UPGRADE: %item%{freq_str} - {reasons}", freq_str=freq_str, reasons=', '.join(upgrade_reason))
            
                if should_copy:
                    log(f"  [UPDATE AUDIO] {src} -> {dest}")
                    logmsg.info("COPY: %item% -> {dest}", dest=str(dest))
                    if not dry_run:
                        shutil.copy2(src, dest)
                        remove_backup_for(rel, dry_run)
                        updated_album_dirs.add(dest.parent)
                else:
                    # Handle non-audio files (artwork, etc.)
                    # Normalize artwork filenames based on location (album vs artist folder)
                    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    is_image = src.suffix.lower() in image_extensions
                    
                    if is_image:
                        # Determine if this is an album folder or artist folder
                        # Album folder: MUSIC_ROOT/Artist/(Year) Album/image.jpg
                        # Artist folder: MUSIC_ROOT/Artist/image.jpg
                        dest_parent = dest.parent
                        dest_grandparent = dest_parent.parent
                        
                        # Check if this is an artist folder (parent's parent is MUSIC_ROOT)
                        is_artist_folder = False
                        try:
                            if dest_grandparent == MUSIC_ROOT:
                                # Artist folder: MUSIC_ROOT/Artist/image.jpg
                                is_artist_folder = True
                            else:
                                # Album folder: MUSIC_ROOT/Artist/(Year) Album/image.jpg
                                # Parent should have audio files (it's an album folder)
                                is_artist_folder = False
                        except (ValueError, AttributeError):
                            # If we can't determine relationship, assume it's an album folder (safer default)
                            is_artist_folder = False
                        
                        if is_artist_folder:
                            # Artist artwork: normalize to folder.jpg (preferred for artists)
                            # Location is already correct (artist folder), just normalize filename
                            normalized_dest = dest_parent / "folder.jpg"
                            log(f"  [UPDATE ARTIST ART] Normalizing {src.name} -> folder.jpg in artist folder")
                            logmsg.verbose("Normalizing %item% -> folder.jpg in artist folder")
                            dest = normalized_dest
                        else:
                            # Album artwork: normalize to cover.jpg
                            # Location is already correct (album folder), just normalize filename
                            normalized_dest = dest_parent / "cover.jpg"
                            log(f"  [UPDATE ALBUM ART] Normalizing {src.name} -> cover.jpg in album folder")
                            logmsg.verbose("Normalizing %item% -> cover.jpg in album folder")
                            dest = normalized_dest
                    
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
                    if is_image and not is_artist_folder and dest.name.lower() == "cover.jpg":
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

    # Add structured logging for updated albums
    for album_dir in updated_album_dirs:
        label = album_label_from_dir(album_dir)
        add_album_event_label(label, "Updated from overlay.")  # Old API
        # Set album context temporarily to log the update event (polymorphic: accepts Path directly)
        try:
            album_key = logmsg.set_album(album_dir)
            try:
                item_key = logmsg.set_item("album_updated")
                try:
                    logmsg.info("Updated from overlay.")
                finally:
                    logmsg.unset_item(item_key)
            finally:
                logmsg.unset_album(album_key)
        except Exception:
            # If path extraction fails, skip structured logging for this album
            pass

    return updated_album_dirs, albums_with_new_cover


def sync_update_root_structure(dry_run: bool = False) -> None:
    """
    Ensure UPDATE_ROOT has the same directory structure as MUSIC_ROOT, but no files.
    Remove any directories in UPDATE_ROOT that don't exist in MUSIC_ROOT.
    """
    if not UPDATE_ROOT or not UPDATE_ROOT.exists():
        return

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


def sync_backups(dry_run: bool = False) -> None:
    """
    Sync backup folder with live files:
    - If backup and live file exist and are identical (same checksum): remove backup
    - If backup exists but live file doesn't: restore backup, then remove backup
    - If backup exists and live file is different: keep backup (live was modified)
    - Clean up empty folders including backup root
    
    Goal: Only keep backups when we have a corresponding live file that is different.
    """
    from config import BACKUP_ROOT, MUSIC_ROOT, CLEAN_EMPTY_BACKUP_FOLDERS
    from logging_utils import log, album_label_from_dir, add_album_event_label, add_album_warning_label
    
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
                # Both backup and live file exist - compare checksums
                backup_checksum = file_checksum(backup_file)
                live_checksum = file_checksum(live_file)
                
                if backup_checksum and live_checksum:
                    if backup_checksum == live_checksum:
                        # Files are identical - remove backup (no need to keep it)
                        log(f"  [SYNC BACKUP] Files identical, removing backup: {backup_file.name}")
                        if not dry_run:
                            try:
                                backup_file.unlink()
                                backups_removed += 1
                            except Exception as e:
                                log(f"    [WARN] Could not remove backup {backup_file}: {e}")
                    else:
                        # Files are different - keep backup (live file was modified)
                        log(f"  [SYNC BACKUP] Files differ, keeping backup: {backup_file.name}")
                else:
                    # Couldn't calculate checksums - keep backup to be safe
                    log(f"  [SYNC BACKUP] Could not compare checksums, keeping backup: {backup_file.name}")
            else:
                # Backup exists but live file doesn't - restore it
                log(f"  [SYNC BACKUP] Live file missing, restoring: {backup_file.name} -> {live_file}")
                if not dry_run:
                    try:
                        live_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup_file, live_file)
                        backups_restored += 1
                        
                        # Remove backup after restoring
                        try:
                            backup_file.unlink()
                            backups_removed += 1
                        except Exception as e:
                            log(f"    [WARN] Could not delete backup after restore {backup_file}: {e}")
                        
                        label = album_label_from_dir(live_file.parent)
                        add_album_event_label(label, "Restored missing file from backup.")
                    except Exception as e:
                        log(f"    [WARN] Could not restore {backup_file}: {e}")
                        label = album_label_from_dir(live_file.parent)
                        add_album_warning_label(label, f"[WARN] Could not restore {backup_file}: {e}")
        
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

