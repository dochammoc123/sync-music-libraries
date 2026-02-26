"""
File operations: moving, organizing, and cleaning up files.
"""
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import (
    AUDIO_EXT,
    ARCHIVE_EXTENSIONS,
    CLEAN_EMPTY_DOWNLOAD_FOLDERS,
    CLEANUP_EXTENSIONS,
    CLEANUP_FILENAMES,
    DOWNLOADS_DIR,
    MUSIC_ROOT,
)
from logging_utils import album_label_from_tags
from tag_operations import choose_album_year, format_track_filename, sanitize_filename_component, write_tags_to_file


def make_album_dir(root: Path, artist: str, album: str, year: str, dry_run: bool = False) -> Path:
    """Create an album directory path and optionally create it."""
    safe_artist = sanitize_filename_component(artist)
    disp_year = f"({year}) " if year else ""
    safe_album = sanitize_filename_component(album)
    album_dir = root / safe_artist / (disp_year + safe_album)
    if not dry_run:
        album_dir.mkdir(parents=True, exist_ok=True)
    return album_dir


def cleanup_download_dirs_for_album(items: List[Tuple[Path, Dict[str, Any]]], dry_run: bool = False, used_artwork_files: List[Path] = None, processed_audio_files: List[Path] = None, extracted_archives: List[Path] = None) -> None:
    """
    After we've moved an album's audio files out of Downloads/Music
    and processed its artwork, clean up leftover images and junk files,
    then remove any now-empty directories (including empty parent dirs),
    stopping at DOWNLOADS_DIR.
    """
    from tag_operations import find_root_album_directory
    from config import DOWNLOADS_DIR
    from structured_logging import logmsg  # Global singleton - always available
    
    # Collect root directories (where files were flattened to)
    all_files = [p for (p, _tags) in items]
    root_dirs = set()
    for p, _tags in items:
        root_dir = find_root_album_directory(p, all_files, DOWNLOADS_DIR)
        original_root = root_dir
        # If root_dir is a CD/original subdir of an album, walk up to the album directory
        # so we clean up the whole album folder (CD1, CD2, original, etc.).
        # Do NOT walk up to artist when parent has multiple albums (19, 21, 25) - we only
        # clean the album we just processed, not sibling albums.
        while True:
            parent = root_dir.parent
            if parent == root_dir or (DOWNLOADS_DIR and parent.resolve() == DOWNLOADS_DIR.resolve()):
                break
            try:
                if parent.exists():
                    # Only walk up when we're inside a CD/original-style subdir of an album
                    name_upper = root_dir.name.upper()
                    if name_upper.startswith("CD") or root_dir.name.lower() == "original":
                        root_dir = parent
                        continue
            except (OSError, PermissionError):
                pass
            break
        root_dirs.add(root_dir)
    
    # Process each root directory (cleanup will recursively handle subdirectories)
    dirs = sorted(root_dirs, key=lambda d: len(str(d)), reverse=True)
    

    for d in dirs:
        if not d.exists():
            continue

        if dry_run:
            try:
                remaining = [f.name for f in d.rglob("*")]
            except FileNotFoundError:
                remaining = []
            continue

        # Recursively process all files in root directory and subdirectories
        files_found_count = 0
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            files_found_count += 1
                
            name = f.name
            suffix = f.suffix.lower()
            
            # Set item context with relative path from downloads root
            try:
                rel_path = f.relative_to(DOWNLOADS_DIR)
                item_id = str(rel_path)
            except ValueError:
                # Fallback to filename if relative path can't be calculated
                item_id = f.name
            
            item_key = logmsg.begin_item(item_id)
            try:
                # Check if file is in a subdirectory (not directly in the root album directory)
                file_parent = f.parent
                try:
                    # Check if file is in a subdirectory by comparing resolved paths
                    is_in_subdirectory = file_parent.resolve() != d.resolve()
                except (OSError, FileNotFoundError):
                    # Can't resolve paths, assume not in subdirectory to be safe
                    is_in_subdirectory = False
                
                # Handle artwork files in DOWNLOADS_DIR root:
                # - If the artwork was matched/used for this album, remove it
                # - If the artwork wasn't matched (no album found), preserve it for future albums
                is_in_downloads_root = f.parent.resolve() == DOWNLOADS_DIR.resolve()
                is_artwork_file = suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                
                # If file is in a subdirectory, check if it was already processed before removing
                if is_in_subdirectory:
                    # Safety check: Only remove files that were already processed/used
                    # This prevents deleting files that are still needed
                    is_audio_file = suffix in AUDIO_EXT
                    should_remove = False
                    removal_reason = ""
                    
                    if is_audio_file:
                        # Check if this audio file was already processed (moved, upgraded, or skipped)
                        processed_audio = processed_audio_files or []
                        if any(f.resolve() == audio.resolve() for audio in processed_audio):
                            should_remove = True
                            removal_reason = "processed audio file"
                            logmsg.info("Removing processed audio file in subdirectory: %item%")
                        else:
                            # Audio in a subdir we're cleaning that wasn't part of this album's move (e.g. nested CD/original)
                            logmsg.verbose("Keeping audio in subdirectory (not part of this album): %item%")
                            continue
                    elif is_artwork_file:
                        # For artwork files in subdirectories, always remove them (they're leftovers)
                        # This is safe because artwork processing already found the best artwork from the root directory
                        # Even if this file wasn't "used", it's a duplicate/leftover in a subdirectory
                        should_remove = True
                        used_artwork = used_artwork_files or []
                        if any(f.resolve() == art.resolve() for art in used_artwork):
                            removal_reason = "used artwork file"
                            logmsg.info("Removing used artwork file in subdirectory: %item%")
                        else:
                            removal_reason = "unused artwork file (leftover)"
                            logmsg.info("Removing unused artwork file in subdirectory: %item%")
                    else:
                        # Non-audio, non-artwork file in subdirectory - remove it (leftover from processing)
                        should_remove = True
                        removal_reason = "leftover file"
                        logmsg.info("Removing leftover file in subdirectory: %item%")
                    
                    if should_remove:
                        if dry_run:
                            # In dry run, log what would be removed
                            logmsg.info("Would remove %item% ({reason})", reason=removal_reason)
                        else:
                            try:
                                f.unlink()
                            except Exception as e:
                                logmsg.warn("Could not delete %item%: {error}", error=str(e))
                    continue
                
                if is_artwork_file and is_in_downloads_root:
                    used_artwork = used_artwork_files or []
                    # Check if this artwork file was used/matched for this album
                    if any(f.resolve() == art.resolve() for art in used_artwork):
                        # This artwork was matched and used - remove it
                        logmsg.info("Removing matched artwork from download root: %item%")
                        if not dry_run:
                            try:
                                f.unlink()
                            except Exception as e:
                                logmsg.warn("Could not delete %item%: {error}", error=str(e))
                    else:
                        # This artwork wasn't matched - preserve it for future albums
                        logmsg.verbose("Preserving unmatched artwork in download root (may be for future album): %item%")
                    continue

                # Remove processed audio files (moved, upgraded, or skipped)
                # These files were matched to an album and processed, so they should be cleaned up
                is_audio_file = suffix in AUDIO_EXT
                if is_audio_file:
                    processed_audio = processed_audio_files or []
                    # Check if this audio file was processed (moved, upgraded, or skipped)
                    if any(f.resolve() == audio.resolve() for audio in processed_audio):
                        # This audio file was processed - remove it
                        logmsg.info("Removing processed audio file: %item%")
                        if not dry_run:
                            try:
                                f.unlink()
                            except Exception as e:
                                logmsg.warn("Could not delete %item%: {error}", error=str(e))
                        continue

                # Remove files with cleanup extensions (incomplete downloads, leftover images, archives, etc.)
                # ZIP files and other cleanup extensions should be removed consistently
                # Only artwork files in downloads root are special (preserve if unmatched)
                if suffix in CLEANUP_EXTENSIONS:
                    # Artwork files in downloads root are handled above (preserve if unmatched)
                    if is_artwork_file and is_in_downloads_root:
                        # Already handled above - skip
                        continue
                    
                    # Remove cleanup extension files (ZIP, partial downloads, etc.)
                    # No special case needed - just remove them regardless of location
                    logmsg.info("Removing file: %item%")
                    if not dry_run:
                        try:
                            f.unlink()
                        except Exception as e:
                            logmsg.warn("Could not delete %item%: {error}", error=str(e))
                    continue

                # Remove files with cleanup filenames (system junk files)
                if name in CLEANUP_FILENAMES:
                    logmsg.info("Removing file: %item%")
                    if not dry_run:
                        try:
                            f.unlink()
                        except Exception as e:
                            logmsg.warn("Could not delete %item%: {error}", error=str(e))
                    continue
                
                # If we get here, the file is in the root album directory and doesn't match any cleanup criteria
                # This shouldn't happen often, but log it for debugging
                logmsg.verbose("Skipping file in root album directory (not processed): %item%")
            finally:
                logmsg.end_item(item_key)

        # Remove empty subdirectories first (deepest first)
        # Use rglob to find all subdirectories recursively, then sort by depth (deepest first)
        try:
            # Collect all subdirectories first
            subdirs = []
            for item in d.rglob("*"):
                if item.is_dir():
                    subdirs.append(item)
            
            # Sort by path length (deepest first) so we remove nested folders before parent folders
            subdirs.sort(key=lambda p: len(str(p)), reverse=True)
            
            for subdir in subdirs:
                try:
                    # Check if directory still exists (might have been removed as parent of deeper folder)
                    if not subdir.exists():
                        continue
                    
                    contents = list(subdir.iterdir())
                    if not contents:
                        # Directory is empty - remove it
                        # Set item context with relative path from downloads root
                        try:
                            rel_path = subdir.relative_to(DOWNLOADS_DIR)
                            folder_item_id = str(rel_path)
                        except ValueError:
                            # Fallback to folder name if relative path can't be calculated
                            folder_item_id = subdir.name
                        
                        folder_item_key = logmsg.begin_item(folder_item_id)
                        try:
                            logmsg.info("Removing empty download folder: %item%")
                            if not dry_run:
                                subdir.rmdir()
                        finally:
                            logmsg.end_item(folder_item_key)
                    else:
                        # Directory still has contents - log for debugging
                        logmsg.verbose("Skipping non-empty folder: {folder} (contents: {count} items)", folder=subdir.name, count=len(contents))
                except (OSError, PermissionError, FileNotFoundError) as e:
                    logmsg.verbose("Could not access folder {folder}: {error}", folder=subdir.name, error=str(e))
                    pass  # Skip if we can't access it
        except (OSError, PermissionError) as e:
            logmsg.verbose("Error during folder cleanup: {error}", error=str(e))
            pass

        # Then walk up and remove empty parent directories
        current = d
        while True:
            try:
                if current.resolve() == DOWNLOADS_DIR.resolve():
                    break
            except FileNotFoundError:
                break

            try:
                contents = list(current.iterdir())
            except FileNotFoundError:
                break

            if contents:
                remaining = []
                for f in contents:
                    # Set item context once per iteration (for all logs in this iteration)
                    item_key = None
                    if f.is_file():
                        # Try to use relative path from downloads root, fallback to filename
                        try:
                            rel_path = f.relative_to(DOWNLOADS_DIR)
                            item_id = str(rel_path)
                        except ValueError:
                            # File is outside downloads root, use filename
                            item_id = f.name
                        item_key = logmsg.begin_item(item_id)
                        
                        # Handle artwork files in DOWNLOADS_DIR root:
                        # - If the artwork was matched/used for this album, remove it
                        # - If the artwork wasn't matched (no album found), preserve it for future albums
                        is_in_downloads_root = f.parent.resolve() == DOWNLOADS_DIR.resolve()
                        is_artwork_file = f.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                        is_audio_file = f.suffix.lower() in AUDIO_EXT
                        
                        if is_artwork_file and is_in_downloads_root:
                            used_artwork = used_artwork_files or []
                            # Check if this artwork file was used/matched for this album
                            if any(f.resolve() == art.resolve() for art in used_artwork):
                                # This artwork was matched and used - remove it
                                logmsg.info("Removing matched artwork from download root: %item%")
                                if not dry_run:
                                    try:
                                        f.unlink()
                                    except Exception as e:
                                        logmsg.warn("Could not delete %item%: {error}", error=str(e))
                                logmsg.end_item(item_key)
                            else:
                                # This artwork wasn't matched - preserve it for future albums
                                logmsg.verbose("Preserving unmatched artwork in download root (may be for future album): %item%")
                                logmsg.end_item(item_key)
                                remaining.append(f)
                        elif is_audio_file and is_in_downloads_root:
                            # Handle processed audio files in DOWNLOADS_DIR root
                            processed_audio = processed_audio_files or []
                            if any(f.resolve() == audio.resolve() for audio in processed_audio):
                                # This audio file was processed - remove it
                                logmsg.info("Removing processed audio file from download root: %item%")
                                if not dry_run:
                                    try:
                                        f.unlink()
                                    except Exception as e:
                                        logmsg.warn("Could not delete %item%: {error}", error=str(e))
                                logmsg.end_item(item_key)
                            else:
                                # This audio file wasn't processed - preserve it (may be for future album)
                                logmsg.end_item(item_key)
                                remaining.append(f)
                        elif f.name in CLEANUP_FILENAMES or f.suffix.lower() in CLEANUP_EXTENSIONS:
                            logmsg.info("Removing file: %item%")
                            try:
                                if not dry_run:
                                    f.unlink()
                            except Exception as e:
                                logmsg.warn("Could not delete %item%: {error}", error=str(e))
                                logmsg.end_item(item_key)
                                remaining.append(f)
                            else:
                                logmsg.end_item(item_key)
                        else:
                            logmsg.end_item(item_key)
                            remaining.append(f)
                    else:
                        remaining.append(f)

                if remaining:
                    break

            # Set item context with relative path from downloads root
            try:
                rel_path = current.relative_to(DOWNLOADS_DIR)
                folder_item_id = str(rel_path)
            except ValueError:
                # Fallback to folder name if relative path can't be calculated
                folder_item_id = current.name
            
            folder_item_key = logmsg.begin_item(folder_item_id)
            logmsg.info("Removing empty download folder: %item%")
            try:
                if not dry_run:
                    current.rmdir()
            except Exception as e:
                logmsg.warn("Could not remove %item%: {error}", error=str(e))
                logmsg.end_item(folder_item_key)
                break
            logmsg.end_item(folder_item_key)

            current = current.parent


def move_booklets_from_downloads(items: List[Tuple[Path, Dict[str, Any]]], album_dir: Path, dry_run: bool = False) -> None:
    """
    Look for PDF "digital booklet" files in the download directories for
    this album and move them into the album_dir in the library.
    
    Example:
        C:\\Users\\...\\Downloads\\Music\\Arcade Fire\\Reflektor\\Digital Booklet - Reflektor.pdf
        -> \\\\ROCK\\...\\Music\\Arcade Fire\\(2013) Reflektor\\Digital Booklet - Reflektor.pdf
    """
    from structured_logging import logmsg
    
    # All the source dirs that contained the album's audio files
    candidate_dirs = {p.parent for (p, _tags) in items}
    
    for d in candidate_dirs:
        if not d.exists():
            continue
        
        # Case-insensitive .pdf
        for pdf in d.glob("*.pdf"):
            dest = album_dir / pdf.name
            item_key = logmsg.begin_item(pdf.name)
            logmsg.info("MOVE: %item% -> {dest}", dest=str(dest))
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(pdf), str(dest))
            logmsg.end_item(item_key)


def move_album_from_downloads(
    album_key: Tuple[str, str],
    items: List[Tuple[Path, Dict[str, Any]]],
    music_root: Path,
    dry_run: bool = False,
    extracted_archives: List[Path] = None
) -> None:
    """Move an album from downloads to the music library, organizing files."""
    from artwork import find_predownloaded_art_source_for_album
    
    artist, album = album_key
    year = choose_album_year(items)
    label = album_label_from_tags(artist, album, year)
    
    from structured_logging import logmsg

    album_dir = make_album_dir(music_root, artist, album, year, dry_run)
    existing = album_dir.exists()

    # Events tracked automatically by structured logging


    # Push organizing header (nested under album)
    organize_key = logmsg.push_header("Organizing tracks", "%msg% (%count% tracks)", "DOWNLOAD")
    try:
        logmsg.verbose("Target directory: {album_dir}", album_dir=str(album_dir))

        items_sorted = sorted(items, key=lambda x: (x[1]["discnum"], x[1]["tracknum"]))
        discs = set(t["discnum"] for _, t in items)
        
        # Track which audio files were processed (moved, upgraded, or skipped)
        # These should be cleaned up from downloads
        processed_audio_files = []
        
        # Track destination files for use after moving (for artwork export, etc.)
        dest_items = []
        
        # Get album metadata from files that have tags (for filling in missing tags)
        album_metadata = None
        for _, t in items:
            if t.get("artist") and t.get("album") and t.get("year"):
                album_metadata = {
                    "artist": t["artist"],
                    "album": t["album"],
                    "year": t["year"],
                }
                break

        for src, tags in items_sorted:
            ext = src.suffix
            
            # Try to read tags from source file for filename generation
            # Don't write tags yet - that happens later after backup during embed step
            tags_to_use = tags.copy()
            try:
                from tag_operations import get_tags
                original_tags = get_tags(src)
                if original_tags and original_tags.get("title") and original_tags.get("tracknum", 0) > 0:
                    # File has good tags, use them for filename
                    tags_to_use = original_tags.copy()
            except Exception:
                # Can't read tags, use fallback tags for filename
                pass
            
            # Generate filename from tags (title and tracknum from tags, not filename)
            # If tags are missing, filename will use fallback values (from path/filename parsing)
            filename = format_track_filename(tags_to_use, ext)
            if len(discs) > 1:
                disc_label = f"CD{tags_to_use['discnum']}"
                disc_dir = album_dir / disc_label
                if not dry_run:
                    disc_dir.mkdir(exist_ok=True)
                dest = disc_dir / filename
            else:
                dest = album_dir / filename

            # Check if destination exists - compare frequency first, then file size
            # Handles partial/corrupted files and frequency upgrades
            # Note: We can't detect truncated files (missing last 10 seconds) without a reference file
            # File size comparison helps when we have duplicates of the same song/quality
            # We also check for suspiciously small file sizes (heuristic warning)
            should_move = True
            
            # Set item context for this track
            item_key = logmsg.begin_item(str(src))
            try:
                # Check source file for size warnings (may indicate truncation)
                from tag_operations import check_file_size_warning
                size_warning = check_file_size_warning(src)
                if size_warning:
                    level, message = size_warning
                    if level == "WARN":
                        logmsg.warn("{file}: {warning_msg}", file=src.name, warning_msg=message)
                    else:
                        logmsg.info("{file}: {warning_msg}", file=src.name, warning_msg=message)
                
                # Always check source file properties
                from tag_operations import get_sample_rate, get_audio_duration, get_tags, check_file_size_warning
                src_size = src.stat().st_size
                src_freq = get_sample_rate(src)
                src_duration = get_audio_duration(src)
                src_size_warning = check_file_size_warning(src)
                src_is_truncated = (src_size_warning is not None)
                
                # Check destination file properties only if it exists
                # On network paths (UNC), dest.exists() might raise an exception instead of returning False
                dest_exists = False
                dest_size = 0
                dest_freq = None
                dest_duration = None
                dest_tags = None
                dest_is_corrupt = False
                dest_is_truncated = False
                upgrade_reason = []
                
                try:
                    dest_exists = dest.exists()
                except (OSError, FileNotFoundError):
                    # Network path might not be accessible or file doesn't exist
                    dest_exists = False
                
                if dest_exists:
                    try:
                        # First check if existing file is corrupt (can't read tags) or truncated
                        # If corrupt, always upgrade (any working file is better than corrupt)
                        # If truncated, upgrade if incoming is better (not truncated, or larger if both truncated)
                        dest_tags = get_tags(dest)
                        dest_is_corrupt = (dest_tags is None)
                        dest_size_warning = check_file_size_warning(dest)
                        dest_is_truncated = (dest_size_warning is not None)
                        dest_size = dest.stat().st_size
                        dest_freq = get_sample_rate(dest)
                        dest_duration = get_audio_duration(dest)
                        
                        # If existing file is corrupt, always upgrade (any working file is better)
                        if dest_is_corrupt:
                            upgrade_reason = ["existing file is corrupt (cannot read tags)"]
                            should_move = True
                        elif dest_is_truncated:
                            # Existing file is truncated - upgrade if incoming is better
                            if not src_is_truncated:
                                # Incoming is not truncated - upgrade
                                upgrade_reason = ["existing file is truncated, incoming file is complete"]
                                should_move = True
                            elif src_size > dest_size:
                                # Both truncated, but incoming is larger - upgrade
                                upgrade_reason = [f"existing file is truncated, incoming is larger (size: {src_size} > {dest_size} bytes)"]
                                should_move = True
                            else:
                                # Both truncated, incoming is not better - skip
                                should_move = False
                                logmsg.info("SKIP: %item% (existing file is truncated, but incoming is also truncated and not larger)")
                        else:
                            # Compare: frequency first, then file size
                            # Duration can help detect truncation, but metadata duration may be wrong
                            upgrade_reason = []
                            if src_freq and dest_freq:
                                if src_freq > dest_freq:
                                    upgrade_reason.append(f"frequency: {src_freq}Hz > {dest_freq}Hz")
                                    should_move = True
                                elif src_freq < dest_freq:
                                    should_move = False
                                    logmsg.info("SKIP: %item% (existing has higher frequency: {dest_freq}Hz > {src_freq}Hz)", dest_freq=dest_freq, src_freq=src_freq)
                                else:
                                    # Same frequency, compare file size (more reliable than duration for detecting truncation)
                                    if src_size > dest_size:
                                        upgrade_reason.append(f"size: {src_size} > {dest_size} bytes")
                                        # Also check duration if available (though metadata may be wrong for truncated files)
                                        if src_duration and dest_duration:
                                            duration_diff = src_duration - dest_duration
                                            if abs(duration_diff) > 5:  # More than 5 seconds difference
                                                upgrade_reason.append(f"duration: {src_duration:.1f}s vs {dest_duration:.1f}s")
                                        should_move = True
                                    else:
                                        should_move = False
                                        logmsg.info("SKIP: %item% (same frequency {freq}Hz, existing file is larger or equal: {dest_size} >= {src_size} bytes)", freq=src_freq, dest_size=dest_size, src_size=src_size)
                            else:
                                # Can't determine frequency, fall back to file size only
                                if src_size > dest_size:
                                    upgrade_reason.append(f"size: {src_size} > {dest_size} bytes")
                                    if src_duration and dest_duration:
                                        duration_diff = src_duration - dest_duration
                                        if abs(duration_diff) > 5:
                                            upgrade_reason.append(f"duration: {src_duration:.1f}s vs {dest_duration:.1f}s")
                                    should_move = True
                                else:
                                    should_move = False
                                    logmsg.info("SKIP: %item% (existing file is larger or equal: {dest_size} >= {src_size} bytes)", dest_size=dest_size, src_size=src_size)
                    except (OSError, FileNotFoundError) as e:
                        # File might have been deleted or become inaccessible between exists() check and stat()
                        # Treat as if destination doesn't exist
                        dest_exists = False
                        logmsg.verbose("Destination file became inaccessible during check: %item% ({error})", error=str(e))
                
                if not dest_exists:
                    # Destination doesn't exist - initialize upgrade_reason for new file
                    upgrade_reason = []
                
                if should_move and upgrade_reason:
                    freq_str = f" ({src_freq}Hz vs {dest_freq}Hz)" if dest_exists and src_freq and dest_freq else ""
                    logmsg.info("UPGRADE: %item%{freq_str} - {reasons}", freq_str=freq_str, reasons=', '.join(upgrade_reason))

                if should_move:
                    logmsg.info("MOVE: %item% -> {dest}", dest=str(dest))
                    if not dry_run:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dest))
                        # Remove any existing backup - this is a new original, backup is obsolete
                        try:
                            rel = dest.relative_to(music_root)
                            from sync_operations import remove_backup_for
                            remove_backup_for(rel, dry_run)
                        except (ValueError, Exception) as e:
                            # If we can't determine relative path or remove backup, log but continue
                            logmsg.verbose("Could not remove backup for new original: {error}", error=str(e))
                    # Track destination file for use after moving (for artwork export, etc.)
                    dest_items.append((dest, tags_to_use))
                    # Track that this file was processed (moved)
                    processed_audio_files.append(src)
                else:
                    # File was skipped (better version exists) - use existing destination
                    dest_items.append((dest, tags_to_use))
                    # File was skipped (better version exists) - still mark as processed for cleanup
                    processed_audio_files.append(src)
            finally:
                logmsg.end_item(item_key)
    finally:
        # Pop organizing header
        logmsg.pop_header(organize_key)
    
    # Move digital booklets (PDFs) from download directories to album folder
    move_booklets_from_downloads(items, album_dir, dry_run)
    
    # Push artwork header
    art_key = logmsg.push_header("Processing album artwork", "%msg% (%count% items)", "ARTWORK", always_show=True)
    try:
        # Find best art file (standard names + pattern-matched, always largest)
        # This function now handles:
        # - Standard art files (large_cover.jpg, cover.jpg)
        # - Pattern-matched art (e.g., "pure-heroine-lorde.jpg")
        # - Always selects largest by pixel dimensions, then file size
        # IMPORTANT: Always check for pattern-matched art, even if cover.jpg exists,
        # to upgrade if the new art is larger (by pixel dimensions, then file size)
        predownloaded_art = find_predownloaded_art_source_for_album(items)
        used_predownloaded_art = predownloaded_art is not None
        # Log artwork selection (structured logging - detail log only via verbose)
        if used_predownloaded_art and predownloaded_art:
            from artwork import get_image_size
            art_size = get_image_size(predownloaded_art)
            if art_size:
                file_size = predownloaded_art.stat().st_size if predownloaded_art.exists() else 0
                logmsg.verbose("Selected best art: {name} ({width}x{height}, {size} bytes)", name=predownloaded_art.name, width=art_size[0], height=art_size[1], size=file_size)
        
        # Also check for folder.jpg separately (may be different from cover)
        from tag_operations import find_root_album_directory
        from config import DOWNLOADS_DIR
        all_files = [p for (p, _tags) in items]
        root_dirs = set()
        child_dirs = set()
        for p, _tags in items:
            root_dir = find_root_album_directory(p, all_files, DOWNLOADS_DIR)
            root_dirs.add(root_dir)
            if p.parent != root_dir:
                child_dirs.add(p.parent)
        
        # Check for folder.jpg: prioritize root directories (parent as source of truth)
        # NOTE: We do NOT copy folder.jpg from CD1/CD2 subfolders to album root
        # CD1/CD2 subfolders should keep their own folder.jpg files
        predownloaded_folder = None
        for d in sorted(root_dirs, key=lambda x: len(str(x))):
            folder_candidate = d / "folder.jpg"
            if folder_candidate.exists():
                predownloaded_folder = folder_candidate
                break
        # Only check child directories (CD1/CD2) if no root folder.jpg found
        # But we won't copy it to album root - it stays in the subfolder
        if not predownloaded_folder:
            for d in sorted(child_dirs, key=lambda x: len(str(x))):
                folder_candidate = d / "folder.jpg"
                if folder_candidate.exists():
                    # Found folder.jpg in CD1/CD2 - don't copy to album root, leave it there
                    # Only use it if we need to create cover.jpg and no other art exists
                    predownloaded_folder = folder_candidate
                    break

        # Always check for artwork, even if cover.jpg exists (to upgrade if new art is larger)
        # This ensures we find pattern-matched artwork like "pure-heroine-lorde.jpg"
        cover_dest = album_dir / "cover.jpg"
        folder_dest = album_dir / "folder.jpg"
        
        # ALWAYS check for pattern-matched art, even if cover.jpg exists
        # This handles cases where pattern-matched art is in downloads root and might be larger
        if not used_predownloaded_art:
            # Re-check for pattern-matched art (might be in downloads root)
            predownloaded_art = find_predownloaded_art_source_for_album(items)
            used_predownloaded_art = predownloaded_art is not None
            # Log artwork selection (structured logging - detail log only via verbose)
            if used_predownloaded_art and predownloaded_art:
                from artwork import get_image_size
                art_size = get_image_size(predownloaded_art)
                if art_size:
                    file_size = predownloaded_art.stat().st_size if predownloaded_art.exists() else 0
                    logmsg.verbose("Selected best art: {name} ({width}x{height}, {size} bytes)", name=predownloaded_art.name, width=art_size[0], height=art_size[1], size=file_size)
        
        if used_predownloaded_art or predownloaded_folder:
            if not dry_run:
                cover_dest.parent.mkdir(parents=True, exist_ok=True)
                
                # Copy best art to cover.jpg (convert format if needed, upgrade if larger)
                if predownloaded_art:
                    from artwork import get_image_size
                    art_size = get_image_size(predownloaded_art)
                    size_str = f" ({art_size[0]}x{art_size[1]})" if art_size else ""
                    
                    # Check if we should upgrade existing cover.jpg
                    # Only upgrade if new image has MORE pixels (larger dimensions)
                    # Same pixel dimensions = same quality, regardless of file size (which is just encoding/compression)
                    should_upgrade = True
                    existing_size = None
                    art_item_key = logmsg.begin_item(str(predownloaded_art))
                    try:
                        if cover_dest.exists():
                            existing_size = get_image_size(cover_dest)
                            if art_size and existing_size:
                                existing_pixels = existing_size[0] * existing_size[1]
                                new_pixels = art_size[0] * art_size[1]
                                if new_pixels <= existing_pixels:
                                    should_upgrade = False
                                    logmsg.info("Keeping existing cover.jpg (existing: {existing_px}px, new: {new_px}px - same or smaller dimensions) - %item%", existing_px=existing_pixels, new_px=new_pixels)
                        
                        if should_upgrade:
                            if existing_size:
                                logmsg.info("Upgrading cover.jpg with %item%{size_str}", size_str=size_str)
                            else:
                                logmsg.info("Using %item%{size_str} for cover.jpg", size_str=size_str)
                            
                            # Convert to JPG if needed (PNG, etc.)
                            if predownloaded_art.suffix.lower() in {".png", ".gif", ".webp"}:
                                try:
                                    from PIL import Image
                                    with Image.open(predownloaded_art) as img:
                                        # Convert RGBA to RGB if needed (for PNG with transparency)
                                        if img.mode in ("RGBA", "LA", "P"):
                                            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                                            if img.mode == "P":
                                                img = img.convert("RGBA")
                                            rgb_img.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                                            img = rgb_img
                                        # Save with quality=95 and optimize to strip metadata and reduce file size
                                        img.save(cover_dest, "JPEG", quality=95, optimize=True)
                                        logmsg.verbose("Converted {ext} to cover.jpg (optimized)", ext=predownloaded_art.suffix)
                                except Exception as e:
                                    logmsg.warn("Could not convert %item% to JPG, copying as-is: {error}", error=str(e))
                                    shutil.copy2(predownloaded_art, cover_dest)
                            else:
                                # For existing JPEGs, optimize only if significantly larger (likely has metadata)
                                # Otherwise, preserve original to avoid quality loss from re-encoding
                                if predownloaded_art.suffix.lower() in {".jpg", ".jpeg"}:
                                    src_size = predownloaded_art.stat().st_size
                                    # Only optimize if file is unusually large (likely has metadata or inefficient encoding)
                                    # Threshold: if > 1MB for a typical cover, optimize it
                                    if src_size > 1_000_000:  # 1MB threshold
                                        try:
                                            from PIL import Image
                                            with Image.open(predownloaded_art) as img:
                                                # Re-save with optimization to reduce file size (strips metadata, ensures consistent quality)
                                                img.save(cover_dest, "JPEG", quality=95, optimize=True)
                                                opt_size = cover_dest.stat().st_size
                                                logmsg.verbose("Optimized {name} ({src_size} -> {opt_size} bytes, stripped metadata)", name=predownloaded_art.name, src_size=src_size, opt_size=opt_size)
                                        except Exception as e:
                                            logmsg.warn("Could not optimize {name}, copying as-is: {error}", name=predownloaded_art.name, error=str(e))
                                            shutil.copy2(predownloaded_art, cover_dest)
                                    else:
                                        # Small file, likely already optimized - preserve original
                                        shutil.copy2(predownloaded_art, cover_dest)
                                        logmsg.verbose("Preserved original {name} (already optimized)", name=predownloaded_art.name)
                                else:
                                    # Non-JPEG format - copy as-is (Roon/T8 can handle PNG)
                                    shutil.copy2(predownloaded_art, cover_dest)
                                    logmsg.verbose("Preserved original format: {ext}", ext=predownloaded_art.suffix)
                            
                            if existing_size:
                                new_pixels = art_size[0] * art_size[1] if art_size else 0
                                old_pixels = existing_size[0] * existing_size[1]
                                logmsg.info("Upgraded cover.jpg (new: {new_pixels}px, previous: {old_pixels}px)", new_pixels=new_pixels, old_pixels=old_pixels)
                            
                            # Clean up the source art file if it's in the album directory (MUSIC_ROOT)
                            # This handles pattern-matched art files like "pure-heroine-lorde.jpg" that were copied to cover.jpg
                            # Only clean up if the source file is in the same directory as cover.jpg (not in downloads)
                            try:
                                from config import MUSIC_ROOT
                                if predownloaded_art.exists() and album_dir.resolve() in predownloaded_art.resolve().parents:
                                    # Source art file is in the album directory - clean it up since we've copied it to cover.jpg
                                    logmsg.verbose("Cleaning up source art file: %item%")
                                    if not dry_run:
                                        try:
                                            predownloaded_art.unlink()
                                        except Exception as e:
                                            logmsg.warn("Could not delete source art file %item%: {error}", error=str(e))
                            except Exception:
                                # If we can't determine the path relationship, don't clean up (safer)
                                pass
                    finally:
                        # Always unset the item, whether we upgraded or not
                        logmsg.end_item(art_item_key)
                
                elif predownloaded_folder:
                    # Only folder.jpg exists (no large_cover/cover/pattern-matched art), use it for cover.jpg
                    # Must be elif: if we already upgraded with predownloaded_art (e.g. large_cover.jpg),
                    # do NOT overwrite with folder.jpg (which is usually smaller - would downgrade)
                    # But if it's in a CD1/CD2 subfolder, don't copy it to album root
                    # Check if predownloaded_folder is in a subdirectory (CD1/CD2)
                    is_in_subfolder = False
                    try:
                        rel = predownloaded_folder.relative_to(album_dir)
                        if len(rel.parts) > 1:  # In a subdirectory (CD1/CD2)
                            is_in_subfolder = True
                    except ValueError:
                        pass
                    
                    if predownloaded_folder.exists() and not is_in_subfolder:
                        # Only copy if it's in the root album directory, not in CD1/CD2
                        art_item_key = logmsg.begin_item(str(predownloaded_folder))
                        logmsg.info("Using %item% for cover.jpg")
                        try:
                            # Ensure parent directory exists (should already exist from line 577, but be safe)
                            cover_dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(predownloaded_folder, cover_dest)
                        except (OSError, FileNotFoundError) as e:
                            logmsg.warn("Failed to copy folder.jpg to cover.jpg: {error}", error=str(e))
                            logmsg.verbose("Source: {src}, Destination: {dst}", src=str(predownloaded_folder), dst=str(cover_dest))
                        logmsg.end_item(art_item_key)
                    elif is_in_subfolder:
                        # folder.jpg is in CD1/CD2 - leave it there, don't copy to album root
                        logmsg.verbose("Found folder.jpg in subfolder (CD1/CD2), leaving it there: {path}", path=str(predownloaded_folder))
                    else:
                        logmsg.warn("predownloaded_folder does not exist: {path}", path=str(predownloaded_folder))
            
            # Determine source for folder.jpg:
            # Only create folder.jpg in album root if it doesn't exist
            # If it exists in CD1/CD2 subfolders, leave it there (don't copy to album root)
            # If creating in album root, use same as cover.jpg (unless there's a separate predownloaded_folder in root)
            if not folder_dest.exists():
                # Check if predownloaded_folder is in a subdirectory (CD1/CD2)
                is_folder_in_subfolder = False
                if predownloaded_folder:
                    try:
                        rel = predownloaded_folder.relative_to(album_dir)
                        if len(rel.parts) > 1:  # In a subdirectory (CD1/CD2)
                            is_folder_in_subfolder = True
                    except ValueError:
                        pass
                
                if not dry_run:
                    try:
                        # Ensure parent directory exists
                        folder_dest.parent.mkdir(parents=True, exist_ok=True)
                        
                        if predownloaded_folder and predownloaded_folder != predownloaded_art and not is_folder_in_subfolder:
                            # Separate folder.jpg exists in downloads root - copy it to album root
                            if predownloaded_folder.exists():
                                logmsg.verbose("Creating folder.jpg from separate downloads file (may differ from cover.jpg)")
                                shutil.copy2(predownloaded_folder, folder_dest)
                            else:
                                logmsg.warn("predownloaded_folder does not exist for folder.jpg: {path}", path=str(predownloaded_folder))
                        elif predownloaded_art:
                            # Use same art as cover.jpg for folder.jpg
                            if predownloaded_art.suffix.lower() in {".png", ".gif", ".webp"}:
                                # Already converted to cover.jpg above, just copy it
                                if cover_dest.exists():
                                    shutil.copy2(cover_dest, folder_dest)
                                else:
                                    logmsg.warn("cover_dest does not exist for folder.jpg copy: {path}", path=str(cover_dest))
                            else:
                                if predownloaded_art.exists():
                                    shutil.copy2(predownloaded_art, folder_dest)
                                else:
                                    logmsg.warn("predownloaded_art does not exist for folder.jpg: {path}", path=str(predownloaded_art))
                        elif predownloaded_folder and not is_folder_in_subfolder:
                            # Use folder.jpg for both (only if in root, not CD1/CD2)
                            if predownloaded_folder.exists():
                                shutil.copy2(predownloaded_folder, folder_dest)
                            else:
                                logmsg.warn("predownloaded_folder does not exist for folder.jpg: {path}", path=str(predownloaded_folder))
                        elif cover_dest.exists():
                            # No predownloaded art, but cover.jpg exists - use it for folder.jpg
                            shutil.copy2(cover_dest, folder_dest)
                    except (OSError, FileNotFoundError) as e:
                        logmsg.warn("Failed to create folder.jpg: {error}", error=str(e))
                        logmsg.verbose("folder_dest: {dst}", dst=str(folder_dest))
                else:
                    # Dry run: just log what would be done
                    logmsg.verbose("(DRY RUN) Would create folder.jpg")
            else:
                logmsg.verbose("folder.jpg already exists, preserving it (may differ from cover.jpg)")
        
            # Event tracked automatically by structured logging
        else:
            # Log that we checked for artwork (so header shows something)
            # Use a generic item context for "artwork check"
            art_check_key = logmsg.begin_item("artwork check")
            logmsg.info("No pre-downloaded art files found")
            logmsg.end_item(art_check_key)

        # Cover and folder.jpg are ensured globally in Step 4 (ensure_cover_and_folder_global)
    finally:
        logmsg.pop_header(art_key)

    if CLEAN_EMPTY_DOWNLOAD_FOLDERS:
        # Track which artwork files were used/matched for this album
        used_artwork_files = []
        if predownloaded_art:
            used_artwork_files.append(predownloaded_art)
        if predownloaded_folder:
            used_artwork_files.append(predownloaded_folder)
        
        # Per-album cleanup: remove processed audio, used artwork, empty folders from downloads
        # Step 10 does a final pass too; per-album cleanup runs here so album folders
        # (e.g. Lorde/Pure Heroine) are cleaned right after processing
        cleanup_download_dirs_for_album(
            items,
            dry_run=dry_run,
            used_artwork_files=used_artwork_files if used_artwork_files else None,
            processed_audio_files=processed_audio_files,
            extracted_archives=extracted_archives,
        )


def extract_archives_in_downloads(dry_run: bool = False) -> List[Path]:
    """
    Extract archive files (ZIP, 7z, etc.) found in downloads directory.
    Archives are extracted to a folder with the same name (without extension).
    Extracted files are then processed as normal album folders.
    
    Returns list of extracted archive file paths (for cleanup tracking).
    """
    from config import ARCHIVE_EXTENSIONS
    from structured_logging import logmsg
    
    if not DOWNLOADS_DIR.exists():
        return []
    
    archives_found = []
    for archive_file in DOWNLOADS_DIR.rglob("*"):
        if archive_file.is_file() and archive_file.suffix.lower() in ARCHIVE_EXTENSIONS:
            archives_found.append(archive_file)
    
    if not archives_found:
        return []
    
    
    # Push header for archive extraction
    extract_key = logmsg.push_header("Extracting archives from downloads", "%msg% (%count% files)", "EXTRACT")
    try:
        extracted_archives = []
        for archive_file in archives_found:
            # Extract to a folder with the same name (without extension)
            extract_dir = archive_file.parent / archive_file.stem
            
            item_key = logmsg.begin_item(str(archive_file))
            try:
                if extract_dir.exists():
                    logmsg.info("Skipping (extraction folder already exists: {extract_dir})", extract_dir=extract_dir.name)
                    # Still track it for cleanup if it was already extracted
                    extracted_archives.append(archive_file)
                    continue
                
                
                if dry_run:
                    logmsg.info("[DRY RUN] Would extract %item% to {extract_dir}", extract_dir=str(extract_dir))
                    extracted_archives.append(archive_file)
                    continue
                
                try:
                    import zipfile
                    
                    if archive_file.suffix.lower() == ".zip":
                        extract_dir.mkdir(parents=True, exist_ok=True)
                        with zipfile.ZipFile(archive_file, 'r') as zip_ref:
                            zip_ref.extractall(extract_dir)
                        logmsg.info("Extracted {file_count} file(s) from %item%", file_count=len(zip_ref.namelist()))
                        extracted_archives.append(archive_file)
                    # Add other archive formats here as needed:
                    # elif archive_file.suffix.lower() == ".7z":
                    #     # Use py7zr or subprocess to extract 7z files
                    #     pass
                    else:
                        logmsg.warn("Unsupported archive format: {ext}", ext=archive_file.suffix)
                        continue
                        
                except zipfile.BadZipFile:
                    logmsg.warn("%item% is not a valid ZIP file, skipping")
                    continue
                except Exception as e:
                    logmsg.warn("Could not extract %item%: {error}", error=str(e))
                    continue
            finally:
                logmsg.end_item(item_key)
    finally:
        logmsg.pop_header(extract_key)
    return extracted_archives


def process_downloads(dry_run: bool = False) -> None:
    """Process all albums in the downloads directory."""
    from tag_operations import find_audio_files, group_by_album
    from structured_logging import logmsg
    
    # First, extract any archive files (ZIP, etc.) found in downloads
    # Track extracted archives for cleanup
    extracted_archives = extract_archives_in_downloads(dry_run)
    
    # Scan for audio files
    audio_files = list(find_audio_files(DOWNLOADS_DIR))
    if not audio_files:
        logmsg.info("No audio files found in downloads")
        return

    albums = group_by_album(audio_files, downloads_root=DOWNLOADS_DIR)
    logmsg.verbose("Found {album_count} album(s) in downloads", album_count=len(albums))

    skipped_count = 0
    for idx, (album_key, items) in enumerate(albums.items(), start=1):
        artist, album = album_key
        year = choose_album_year(items)

        # Skip albums we can't reliably determine (prevents incorrect folder creation)
        # This happens when:
        #   1. All files are corrupt/missing tags AND path fallback returns "Unknown Artist/Album"
        #   2. Files are directly in downloads root with no folder structure
        if artist == "Unknown Artist" or album == "Unknown Album":
            skipped_count += 1
            file_paths = [str(item[0]) for item in items]
            
            # Log under a synthetic "Downloads (unmatched)" section so summary groups these together
            unmatched_key = logmsg.begin_album("Downloads", "(unmatched)", "")
            try:
                skip_key = logmsg.begin_item(f"album_{idx}")
                logmsg.warn("Cannot determine artist/album from tags or path structure")
                logmsg.info("Unmatched files are left in download folders so you can add tags or reorganize and re-run")
                for file_path in file_paths[:5]:  # Show first 5 files
                    logmsg.verbose("  - {file_path}", file_path=file_path)
                if len(file_paths) > 5:
                    logmsg.verbose("  ... and {remaining} more file(s)", remaining=len(file_paths) - 5)
                logmsg.info("To match in future runs: add tags or use Artist/Album folder structure, then re-run")
                logmsg.warn("Skipped {n} file(s) in downloads - cannot determine artist/album (files: {files})", n=len(file_paths), files=", ".join([Path(f).name for f in file_paths[:3]]) + ("..." if len(file_paths) > 3 else ""))
                logmsg.end_item(skip_key)
            finally:
                logmsg.end_album(unmatched_key)
            continue

        # Process album: set album context and organize
        album_key_val = logmsg.begin_album(artist, album, year)
        try:
            move_album_from_downloads(album_key, items, MUSIC_ROOT, dry_run, extracted_archives)
        finally:
            logmsg.end_album(album_key_val)
    
    if skipped_count > 0:
        # Use same synthetic section so summary groups with per-skip warnings (use placeholder that isn't "count" - that's reserved)
        unmatched_key = logmsg.begin_album("Downloads", "(unmatched)", "")
        try:
            logmsg.warn("Skipped {skipped_count} album(s) that could not be reliably determined. Unmatched files are left in download folders so you can fix tags or structure and re-run.", skipped_count=skipped_count)
        finally:
            logmsg.end_album(unmatched_key)
    
    # Note: Cleanup is deferred to Step 10 (Cleanup downloads folder)
    # This allows Step 7 (Ensure artist images) to access artist images in downloads before cleanup


def cleanup_downloads_folder(dry_run: bool = False, header_key: str = None) -> None:
    """
    Cleanup downloads folder: match artwork, remove processed files, clean up empty folders.
    This is called as Step 10 (after all processing steps, before Roon refresh).
    
    Args:
        dry_run: If True, log what would be done but don't actually clean up
        header_key: Header key from main.py (for nested headers)
    """
    from structured_logging import logmsg
    
    if not CLEAN_EMPTY_DOWNLOAD_FOLDERS or not DOWNLOADS_DIR.exists():
        return
    
    # Match leftover artwork in downloads root to existing albums in library
    match_root_artwork_to_existing_albums(dry_run)
    
    # Cleanup: Remove processed files and empty folders
    # Note: Main header is set in main.py, we just use nested headers here
    
    # Cleanup: Walk through all directories and files in downloads
    # Remove processed files, empty folders, and leftover files
    # Note: Step 7 (Ensure artist images) has already run, so artist images in downloads
    # can now be cleaned up if they weren't used
    
    # First, clean up files in downloads root
    cleanup_root_key = logmsg.push_header("[INFO] Cleaning up files in downloads root", "%msg% (%count% files)", "CLEANUP")
    try:
        cleanup_count = 0
        for f in DOWNLOADS_DIR.iterdir():
            if not f.is_file():
                continue
            
            # Set item context once per iteration (for all logs in this iteration)
            item_key = logmsg.begin_item(str(f.name))
            
            suffix = f.suffix.lower()
            # Remove files with cleanup extensions (ZIP, partial, etc.)
            # Artwork files are handled separately (preserve if unmatched)
            if suffix in CLEANUP_EXTENSIONS:
                is_artwork_file = suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                if not is_artwork_file:
                    # Remove cleanup extension files (ZIP, partial downloads, etc.)
                    logmsg.info("Removing file: %item%")
                    if not dry_run:
                        try:
                            f.unlink()
                        except Exception as e:
                            logmsg.warn("Could not delete %item%: {error}", error=str(e))
                    logmsg.end_item(item_key)
                    cleanup_count += 1
                else:
                    # Artwork file - skip (handled elsewhere)
                    logmsg.end_item(item_key)
            else:
                # Not a cleanup extension file - skip
                logmsg.end_item(item_key)
        if cleanup_count == 0:
            logmsg.verbose("No cleanup files found")
    finally:
        logmsg.pop_header(cleanup_root_key)
        
        # Clean up artist folders and album folders
        # Walk through all subdirectories in downloads
        # Split into album folders (2 levels: Artist\Album) and artist folders (1 level: Artist)
        import os
        folders_to_check = set()
        for root, dirs, files in os.walk(DOWNLOADS_DIR):
            root_path = Path(root)
            if root_path == DOWNLOADS_DIR:
                continue  # Skip root itself
            folders_to_check.add(root_path)
        
        # Separate album folders (2 levels) from artist folders (1 level)
        album_folders = []
        artist_folders = []
        for folder_path in folders_to_check:
            try:
                rel = folder_path.relative_to(DOWNLOADS_DIR)
                if len(rel.parts) == 2:
                    # Album folder: Artist\Album
                    album_folders.append(folder_path)
                elif len(rel.parts) == 1:
                    # Artist folder: Artist
                    artist_folders.append(folder_path)
            except (ValueError, OSError):
                # Can't determine relative path, treat as artist folder
                artist_folders.append(folder_path)
        
        # Process album folders (with album context)
        cleanup_album_folders_key = logmsg.push_header("[INFO] Cleaning up album folders", "%msg% (%count% items)", "CLEANUP")
        try:
            for folder_path in sorted(album_folders, key=lambda p: len(str(p)), reverse=True):
                _process_cleanup_folder(folder_path, DOWNLOADS_DIR, MUSIC_ROOT, dry_run, is_album_folder=True)
        finally:
            logmsg.pop_header(cleanup_album_folders_key)
        
        # Process artist folders (global context)
        cleanup_artist_folders_key = logmsg.push_header("[INFO] Cleaning up artist folders", "%msg% (%count% items)", "CLEANUP")
        try:
            for folder_path in sorted(artist_folders, key=lambda p: len(str(p)), reverse=True):
                _process_cleanup_folder(folder_path, DOWNLOADS_DIR, MUSIC_ROOT, dry_run, is_album_folder=False)
        finally:
            logmsg.pop_header(cleanup_artist_folders_key)


def _process_cleanup_folder(folder_path: Path, downloads_dir: Path, music_root: Path, dry_run: bool, is_album_folder: bool) -> None:
    """
    Process a single folder for cleanup (remove if empty or only contains cleanup files).
    
    Args:
        folder_path: Path to the folder to check
        downloads_dir: Root downloads directory
        music_root: Root music directory (for setting album context)
        dry_run: If True, log what would be done but don't actually clean up
        is_album_folder: If True, this is an album folder (2 levels) and should have album context
    """
    from structured_logging import logmsg
    
    # Set album context for album folders
    # Try to find the actual album directory in MUSIC_ROOT to get correct album context
    album_key = None
    if is_album_folder:
        try:
            rel = folder_path.relative_to(downloads_dir)
            if len(rel.parts) == 2:
                artist_name = rel.parts[0]
                album_name_from_downloads = rel.parts[1]
                
                # Try to find the actual album directory in MUSIC_ROOT
                # Look in MUSIC_ROOT/Artist for album folders that match
                artist_dir = music_root / artist_name
                if artist_dir.exists() and artist_dir.is_dir():
                    # Search for album folder that contains the album name (partial match)
                    # The actual folder might be "(2023 Remaster) (1973) The Dark Side Of The Moon (50th Anniversary)"
                    # but downloads might be "The Dark Side Of The Moon (50th Anniversary)"
                    for potential_album_dir in artist_dir.iterdir():
                        if potential_album_dir.is_dir():
                            # Check if the album name from downloads is contained in the actual folder name
                            # or vice versa (for partial matches)
                            potential_album_name = potential_album_dir.name
                            if (album_name_from_downloads in potential_album_name or 
                                potential_album_name in album_name_from_downloads):
                                # Found a match - use this actual album directory for context
                                album_key = logmsg.begin_album(potential_album_dir)
                                break
        except (ValueError, OSError):
            # Can't set album context, continue without it
            pass
    
    try:
        # Check if folder is empty or only contains cleanup files
        items = list(folder_path.iterdir())
        if not items:
            # Empty folder - remove it
            item_key = logmsg.begin_item(str(folder_path.relative_to(downloads_dir)))
            logmsg.info("Removing empty folder: %item%")
            if not dry_run:
                try:
                    folder_path.rmdir()
                except Exception as e:
                    logmsg.warn("Could not remove empty folder %item%: {error}", error=str(e))
            logmsg.end_item(item_key)
            return
        
        # Check if folder only contains cleanup files (junk, artwork, archives) - NOT audio/video
        # Do NOT treat audio/video as cleanup: unmatched files (couldn't determine artist/album) must
        # stay so the user can add tags or reorganize and re-run. Only remove truly disposable content.
        all_cleanup = True
        for item in items:
            if item.is_dir():
                all_cleanup = False
                break
            suffix = item.suffix.lower()
            name = item.name
            # Consider only CLEANUP_EXTENSIONS and CLEANUP_FILENAMES as cleanup (junk/artwork/archives)
            # Leave audio/video in place so "fix tags and re-run" works without redownloading
            if suffix not in CLEANUP_EXTENSIONS and name not in CLEANUP_FILENAMES:
                all_cleanup = False
                break
        
        if all_cleanup:
            # Remove only cleanup files and the folder if empty afterward
            item_key = logmsg.begin_item(str(folder_path.relative_to(downloads_dir)))
            for item in items:
                if item.is_file():
                    logmsg.verbose("Removing leftover file: {file}", file=item.name)
                    if not dry_run:
                        try:
                            item.unlink()
                        except Exception as e:
                            logmsg.warn("Could not delete {file}: {error}", file=item.name, error=str(e))
            logmsg.info("Removing folder with only cleanup files: %item%")
            if not dry_run:
                try:
                    folder_path.rmdir()
                except Exception as e:
                    logmsg.warn("Could not remove folder %item%: {error}", error=str(e))
            logmsg.end_item(item_key)
    except (OSError, PermissionError) as e:
        logmsg.verbose("Skipping folder {folder} due to error: {error}", folder=str(folder_path.relative_to(downloads_dir)), error=str(e))
    finally:
        if album_key:
            logmsg.end_album(album_key)


def match_root_artwork_to_existing_albums(dry_run: bool = False) -> None:
    """
    Match leftover artwork files in downloads root to existing albums in the library.
    This handles cases where artwork was downloaded separately (e.g., browser download)
    after the album was already processed.
    
    For each artwork file in downloads root:
      1. Try to match it to an existing album by pattern matching (artist/album in filename)
      2. If matched, check if it's better than existing artwork (larger pixel dimensions)
      3. If better, upgrade the album artwork and remove the root file
      4. If not better but matched, just remove the root file (already have better)
    """
    if not DOWNLOADS_DIR.exists() or not MUSIC_ROOT.exists():
        return
    
    import re
    from artwork import find_art_by_pattern, get_image_size, normalize_for_filename
    
    # Find all artwork files in downloads root
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    root_artwork_files = []
    for f in DOWNLOADS_DIR.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in image_extensions:
            continue
        # Skip standard art filenames (not pattern-matched)
        if f.name.lower() in {"large_cover.jpg", "cover.jpg", "folder.jpg"}:
            continue
        root_artwork_files.append(f)
    
    if not root_artwork_files:
        return
    
    
    from structured_logging import logmsg
    
    # Push header for root artwork matching
    root_art_key = logmsg.push_header("Matching artwork in downloads root to existing albums", "%msg% (%count% files)", "ROOT_ART")
    try:
        # Get all album directories from MUSIC_ROOT
        # Structure: MUSIC_ROOT/Artist/(Year) Album/
        album_dirs = []
        if MUSIC_ROOT.exists():
            for artist_dir in MUSIC_ROOT.iterdir():
                if not artist_dir.is_dir():
                    continue
                for item in artist_dir.iterdir():
                    if item.is_dir():
                        # This is an album directory
                        album_dirs.append(item)
                    elif item.name.upper().startswith("CD"):
                        # Multi-disc album - the parent is the album directory
                        pass
        
        if not album_dirs:
            logmsg.verbose("No existing albums found in library to match against")
            return
        
        # Build a list of (artist, album, album_dir) tuples for matching
        albums_to_match = []
        for album_dir in album_dirs:
            try:
                rel = album_dir.relative_to(MUSIC_ROOT)
                parts = list(rel.parts)
                
                # Skip CD subdirectories
                if parts and parts[-1].upper().startswith("CD") and len(parts) >= 2:
                    parts = parts[:-1]
                    album_dir = album_dir.parent
                
                if len(parts) >= 2:
                    artist = parts[0]
                    album_folder = parts[1]
                    # Extract album name (remove year prefix like "(2012) Album Name")
                    year_match = re.match(r'^\((\d{4})\)\s*(.+)$', album_folder)
                    if year_match:
                        album = year_match.group(2).strip()
                    else:
                        album = album_folder
                    albums_to_match.append((artist, album, album_dir))
            except Exception:
                continue
        
        matched_count = 0
        for art_file in root_artwork_files:
            art_file_stem = art_file.stem.lower()
            
            # Try to match this artwork to an album
            best_match = None
            best_match_score = 0
            
            for artist, album, album_dir in albums_to_match:
                norm_artist = normalize_for_filename(artist)
                norm_album = normalize_for_filename(album)
                
                # Check if filename contains both normalized artist and album
                if norm_album in art_file_stem and norm_artist in art_file_stem:
                    # Calculate a simple match score (prefer longer matches)
                    score = len(norm_album) + len(norm_artist)
                    if score > best_match_score:
                        best_match_score = score
                        best_match = (artist, album, album_dir)
            
            if not best_match:
                continue
            
            artist, album, album_dir = best_match
            cover_path = album_dir / "cover.jpg"
            
            # Set album context and item context
            album_key_val = logmsg.begin_album(artist, album, None)  # Year not needed for matching
            try:
                item_key_val = logmsg.begin_item(str(art_file))
                try:
                    # Get size info for the root artwork
                    root_art_size = get_image_size(art_file)
                    if not root_art_size:
                        logmsg.warn("Could not read image size, skipping")
                        continue
                    
                    root_pixels = root_art_size[0] * root_art_size[1]
                    
                    # Check existing artwork
                    existing_better = False
                    if cover_path.exists():
                        existing_size = get_image_size(cover_path)
                        if existing_size:
                            existing_pixels = existing_size[0] * existing_size[1]
                            if existing_pixels >= root_pixels:
                                existing_better = True
                                logmsg.info("Matched but existing artwork is same or better (existing: {existing_px}px, root: {root_px}px)", existing_px=existing_pixels, root_px=root_pixels)
                                # Remove root file since we already have better/same
                                logmsg.info("Removing matched artwork from downloads root: %item%")
                                if not dry_run:
                                    try:
                                        art_file.unlink()
                                        matched_count += 1
                                    except Exception as e:
                                        logmsg.warn("Could not delete %item%: {error}", error=str(e))
                                continue
                    
                    # Root artwork is better (or no existing artwork) - upgrade
                    logmsg.info("Matched, upgrading artwork (root: {root_px}px)", root_px=root_pixels)
                    # Event tracked automatically by structured logging
                    
                    if not dry_run:
                        # Copy artwork to cover.jpg
                        try:
                            cover_path.parent.mkdir(parents=True, exist_ok=True)
                            
                            # Convert format if needed (PNG/GIF to JPG)
                            if art_file.suffix.lower() in {".png", ".gif", ".webp"}:
                                try:
                                    from PIL import Image
                                    with Image.open(art_file) as img:
                                        # Convert RGBA to RGB if needed (for PNG with transparency)
                                        if img.mode in ("RGBA", "LA", "P"):
                                            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                                            if img.mode == "P":
                                                img = img.convert("RGBA")
                                            rgb_img.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                                            img = rgb_img
                                        img.save(cover_path, "JPEG", quality=95, optimize=True)
                                except Exception as e:
                                    logmsg.warn("Could not convert %item% to JPEG, copying as-is: {error}", error=str(e))
                                    shutil.copy2(art_file, cover_path)
                            else:
                                shutil.copy2(art_file, cover_path)
                            
                            # Also update folder.jpg if it doesn't exist or is the same
                            folder_path = album_dir / "folder.jpg"
                            if not folder_path.exists() or (folder_path.exists() and folder_path.stat().st_size == cover_path.stat().st_size):
                                shutil.copy2(cover_path, folder_path)
                                logmsg.verbose("Updated folder.jpg from upgraded cover.jpg")
                            
                            # Remove the root artwork file
                            art_file.unlink()
                            matched_count += 1
                        except Exception as e:
                            logmsg.warn("Could not upgrade artwork: {error}", error=str(e))
                    else:
                        logmsg.info("[DRY RUN] Would upgrade artwork and remove %item%")
                        matched_count += 1
                finally:
                    logmsg.end_item(item_key_val)
            finally:
                logmsg.end_album(album_key_val)
    finally:
        logmsg.pop_header(root_art_key)


def upgrade_albums_to_flac_only(dry_run: bool = False) -> None:
    """
    Enforce FLAC-only where FLAC exists by removing other audio formats.
    BUT: If FLAC files are corrupt (can't read tags) or truncated, remove the FLAC
    and keep the MP3/other format instead, as any incoming file is an "upgrade".
    """
    from logging_utils import album_label_from_dir
    from structured_logging import logmsg
    from tag_operations import get_tags, check_file_size_warning
    
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        p = Path(dirpath)
        exts = {Path(name).suffix.lower()
                for name in filenames
                if Path(name).suffix.lower() in AUDIO_EXT}
        if ".flac" not in exts:
            continue

        # Set album context
        album_key = logmsg.begin_album(p)
        # Label no longer needed - structured logging handles warnings automatically
        
        did_cleanup = False
        flac_files = []
        other_audio_files = []

        # First, collect all audio files and check FLAC validity
        for name in filenames:
            f = p / name
            ext = f.suffix.lower()
            if ext in AUDIO_EXT:
                if ext == ".flac":
                    flac_files.append(f)
                else:
                    other_audio_files.append(f)

        # Check each FLAC file for corruption/truncation
        # Only remove corrupt FLAC files if there's a replacement (other audio format)
        corrupt_flacs = []
        for flac_file in flac_files:
            # Check if we can read tags
            tags = get_tags(flac_file)
            if tags is None:
                # Check if there's a replacement (other audio format with same base name)
                base_name = flac_file.stem
                has_replacement = any(
                    (p / f"{base_name}{ext}").exists() 
                    for ext in AUDIO_EXT 
                    if ext != ".flac"
                )
                if has_replacement:
                    item_key = logmsg.begin_item(flac_file.name)
                    logmsg.warn("Cannot read tags, will remove FLAC (replacement exists): %item%")
                    corrupt_flacs.append(flac_file)
                    logmsg.end_item(item_key)
                else:
                    logmsg.verbose("Cannot read tags, keeping FLAC (no replacement found): {file}", file=flac_file.name)
                continue
            
            # Check if file is truncated (size warning)
            size_warning = check_file_size_warning(flac_file)
            if size_warning:
                level, message = size_warning
                # Check if there's a replacement (other audio format with same base name)
                base_name = flac_file.stem
                has_replacement = any(
                    (p / f"{base_name}{ext}").exists() 
                    for ext in AUDIO_EXT 
                    if ext != ".flac"
                )
                if has_replacement:
                    # Remove if WARN (definitely truncated) or INFO (suspicious, like Royals at 95.9%)
                    # Any incoming file is an "upgrade" over a truncated FLAC
                    item_key = logmsg.begin_item(flac_file.name)
                    logmsg.warn("File truncated, will remove FLAC (replacement exists): %item% - {message}", message=message)
                    corrupt_flacs.append(flac_file)
                    logmsg.end_item(item_key)
                else:
                    logmsg.verbose("File truncated, keeping FLAC (no replacement found): {file} - {msg}", file=flac_file.name, msg=message)
                continue

        # Remove corrupt/truncated FLAC files (only if replacement exists)
        for corrupt_flac in corrupt_flacs:
            item_key = logmsg.begin_item(corrupt_flac.name)
            logmsg.info("DELETE: %item% (corrupt FLAC, replacement exists)")
            did_cleanup = True
            if not dry_run:
                try:
                    corrupt_flac.unlink()
                except OSError as e:
                    logmsg.warn("Could not delete %item%: {error}", error=str(e))
                    from structured_logging import logmsg
                    logmsg.warn("Could not delete corrupt FLAC {file}: {error}", file=corrupt_flac.name, error=str(e))
            logmsg.end_item(item_key)

        # Only remove non-FLAC files if there's a valid FLAC for that specific track
        # Don't remove a non-FLAC file if its corresponding FLAC was just removed (corrupt/truncated)
        # IMPORTANT: Check for FLACs in the parent album directory and all subdirectories
        # This handles cases where MP4s are in album root and FLACs are in CD1/CD2 (or vice versa)
        valid_flacs = [f for f in flac_files if f not in corrupt_flacs]
        removed_flac_stems = {f.stem for f in corrupt_flacs}  # Track which FLACs were removed
        
        # Collect all valid FLAC stems from this directory and all subdirectories
        # This ensures we match MP4s in album root with FLACs in CD1/CD2 subdirectories
        # Use case-insensitive comparison for filename matching
        parent_album_dir = p
        all_valid_flac_stems = {}  # Dict: lowercase_stem -> original_stem (for case-insensitive matching)
        for flac_file in valid_flacs:
            stem_lower = flac_file.stem.lower()
            all_valid_flac_stems[stem_lower] = flac_file.stem
        
        # Also check subdirectories for FLACs (for multi-disc albums)
        for subdir in dirnames:
            subdir_path = p / subdir
            if subdir_path.is_dir():
                try:
                    for subfile in subdir_path.iterdir():
                        if subfile.is_file() and subfile.suffix.lower() == ".flac":
                            # Check if this FLAC is valid (not corrupt)
                            sub_tags = get_tags(subfile)
                            if sub_tags is not None:
                                # Check for truncation
                                sub_size_warning = check_file_size_warning(subfile)
                                if sub_size_warning is None:
                                    stem_lower = subfile.stem.lower()
                                    all_valid_flac_stems[stem_lower] = subfile.stem
                except (OSError, PermissionError):
                    # Skip if we can't access the subdirectory
                    pass
        
        for other_file in other_audio_files:
            # Check if there's a valid FLAC for this specific track (same base name, case-insensitive)
            # Check both current directory and subdirectories
            base_name = other_file.stem
            base_name_lower = base_name.lower()
            has_valid_flac = base_name_lower in all_valid_flac_stems
            
            # Only remove if there's a valid FLAC for this track
            # Don't remove if the FLAC for this track was just removed (corrupt/truncated)
            # Check removed_flac_stems case-insensitively too
            removed_flac_stems_lower = {s.lower() for s in removed_flac_stems}
            if has_valid_flac and base_name_lower not in removed_flac_stems_lower:
                item_key = logmsg.begin_item(other_file.name)
                logmsg.info("DELETE: %item% (non-FLAC, valid FLAC exists for this track)")
                did_cleanup = True
                if not dry_run:
                    try:
                        other_file.unlink()
                    except OSError as e:
                        logmsg.warn("Could not delete %item%: {error}", error=str(e))
                        from structured_logging import logmsg
                        logmsg.warn("Could not delete {file}: {error}", file=other_file.name, error=str(e))
                logmsg.end_item(item_key)
            else:
                # Keep the non-FLAC file - either no FLAC exists for this track, or the FLAC was corrupt/removed
                if base_name_lower in removed_flac_stems_lower:
                    logmsg.verbose("KEEP: {file} (non-FLAC, FLAC for this track was corrupt/removed)", file=other_file.name)
                else:
                    logmsg.verbose("KEEP: {file} (no valid FLAC for this track)", file=other_file.name)

        # Check if we still have both FLAC and non-FLAC files after cleanup (warning case)
        remaining_audio_files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT]
        remaining_flacs = [f for f in remaining_audio_files if f.suffix.lower() == ".flac"]
        remaining_non_flacs = [f for f in remaining_audio_files if f.suffix.lower() != ".flac"]
        
        if remaining_flacs and remaining_non_flacs:
            # After cleanup, we still have both FLAC and non-FLAC files
            # This could indicate case mismatch or different track names
            logmsg.warn("Album still contains both FLAC and non-FLAC files after cleanup (possible case mismatch or different track names)")
            from structured_logging import logmsg
            logmsg.warn("Album still contains both FLAC and non-FLAC files after cleanup (possible case mismatch or different track names)")
        
        if did_cleanup:
            if corrupt_flacs:
                logmsg.info("FLAC-only cleanup (removed {count} corrupt FLAC(s)).", count=len(corrupt_flacs))
                # Event tracked automatically by structured logging
            # Removed redundant "FLAC-only cleanup." message when no corrupt FLACs
        
        logmsg.end_album(album_key)

