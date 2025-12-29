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
from logging_utils import (
    add_album_event_label,
    album_label_from_tags,
    log,
)
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
    
    # Collect root directories (where files were flattened to)
    all_files = [p for (p, _tags) in items]
    root_dirs = set()
    for p, _tags in items:
        root_dir = find_root_album_directory(p, all_files, DOWNLOADS_DIR)
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
            log(f"[CLEANUP DRY] Would inspect {d} (remaining: {remaining})")
            continue

        # Recursively process all files in root directory and subdirectories
        for f in d.rglob("*"):
            if not f.is_file():
                continue
                
            name = f.name
            suffix = f.suffix.lower()

            # Handle artwork files in DOWNLOADS_DIR root:
            # - If the artwork was matched/used for this album, remove it
            # - If the artwork wasn't matched (no album found), preserve it for future albums
            is_in_downloads_root = f.parent.resolve() == DOWNLOADS_DIR.resolve()
            is_artwork_file = suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            
            if is_artwork_file and is_in_downloads_root:
                used_artwork = used_artwork_files or []
                # Check if this artwork file was used/matched for this album
                if any(f.resolve() == art.resolve() for art in used_artwork):
                    # This artwork was matched and used - remove it
                    log(f"[CLEANUP] Removing matched artwork from download root: {f.name}")
                    if not dry_run:
                        try:
                            f.unlink()
                        except Exception as e:
                            log(f"[CLEANUP WARN] Could not delete {f}: {e}")
                else:
                    # This artwork wasn't matched - preserve it for future albums
                    log(f"[CLEANUP] Preserving unmatched artwork in download root (may be for future album): {f.name}")
                continue

            # Remove processed audio files (moved, upgraded, or skipped)
            # These files were matched to an album and processed, so they should be cleaned up
            is_audio_file = suffix in AUDIO_EXT
            if is_audio_file:
                processed_audio = processed_audio_files or []
                # Check if this audio file was processed (moved, upgraded, or skipped)
                if any(f.resolve() == audio.resolve() for audio in processed_audio):
                    # This audio file was processed - remove it
                    log(f"[CLEANUP] Removing processed audio file: {f.name}")
                    if not dry_run:
                        try:
                            f.unlink()
                        except Exception as e:
                            log(f"[CLEANUP WARN] Could not delete {f}: {e}")
                    continue

            # Remove files with cleanup extensions (incomplete downloads, leftover images, archives, etc.)
            # ZIP files and other cleanup extensions should be removed consistently
            # Only artwork files in downloads root are special (preserve if unmatched)
            if suffix in CLEANUP_EXTENSIONS:
                # Artwork files in downloads root are handled above (preserve if unmatched)
                if is_artwork_file and is_in_downloads_root:
                    # Already handled above - skip
                    continue
                else:
                    # Remove cleanup extension files (ZIP, partial downloads, etc.)
                    # No special case needed - just remove them regardless of location
                    log(f"[CLEANUP] Removing file: {f}")
                    if not dry_run:
                        try:
                            f.unlink()
                        except Exception as e:
                            log(f"[CLEANUP WARN] Could not delete {f}: {e}")
                    continue

            # Remove files with cleanup filenames (system junk files)
            elif name in CLEANUP_FILENAMES:
                log(f"[CLEANUP] Removing file: {f}")
                try:
                    f.unlink()
                except Exception as e:
                    log(f"[CLEANUP WARN] Could not delete {f}: {e}")
                continue

        # Remove empty subdirectories first (deepest first)
        try:
            for subdir in sorted(d.rglob("*"), key=lambda p: len(str(p)), reverse=True):
                if subdir.is_dir():
                    try:
                        contents = list(subdir.iterdir())
                        if not contents:
                            log(f"[CLEANUP] Removing empty download folder: {subdir}")
                            if not dry_run:
                                subdir.rmdir()
                    except (OSError, PermissionError, FileNotFoundError):
                        pass  # Skip if we can't access it
        except (OSError, PermissionError):
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
                    if f.is_file():
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
                                log(f"[CLEANUP] Removing matched artwork from download root: {f.name}")
                                if not dry_run:
                                    try:
                                        f.unlink()
                                    except Exception as e:
                                        log(f"[CLEANUP WARN] Could not delete {f}: {e}")
                            else:
                                # This artwork wasn't matched - preserve it for future albums
                                log(f"[CLEANUP] Preserving unmatched artwork in download root (may be for future album): {f.name}")
                                remaining.append(f)
                        elif is_audio_file and is_in_downloads_root:
                            # Handle processed audio files in DOWNLOADS_DIR root
                            processed_audio = processed_audio_files or []
                            if any(f.resolve() == audio.resolve() for audio in processed_audio):
                                # This audio file was processed - remove it
                                log(f"[CLEANUP] Removing processed audio file from download root: {f.name}")
                                if not dry_run:
                                    try:
                                        f.unlink()
                                    except Exception as e:
                                        log(f"[CLEANUP WARN] Could not delete {f}: {e}")
                            else:
                                # This audio file wasn't processed - preserve it (may be for future album)
                                remaining.append(f)
                        elif f.name in CLEANUP_FILENAMES or f.suffix.lower() in CLEANUP_EXTENSIONS:
                            try:
                                log(f"[CLEANUP] Removing file: {f}")
                                if not dry_run:
                                    f.unlink()
                            except Exception as e:
                                log(f"[CLEANUP WARN] Could not delete {f}: {e}")
                                remaining.append(f)
                        else:
                            remaining.append(f)
                    else:
                        remaining.append(f)

                if remaining:
                    break

            log(f"[CLEANUP] Removing empty download folder: {current}")
            try:
                if not dry_run:
                    current.rmdir()
            except Exception as e:
                log(f"[CLEANUP WARN] Could not remove {current}: {e}")
                break

            current = current.parent


def move_album_from_downloads(
    album_key: Tuple[str, str],
    items: List[Tuple[Path, Dict[str, Any]]],
    music_root: Path,
    dry_run: bool = False,
    extracted_archives: List[Path] = None
) -> None:
    """Move an album from downloads to the music library, organizing files."""
    from artwork import ensure_cover_and_folder, find_predownloaded_art_source_for_album
    
    artist, album = album_key
    year = choose_album_year(items)
    label = album_label_from_tags(artist, album, year)

    album_dir = make_album_dir(music_root, artist, album, year, dry_run)
    existing = album_dir.exists()

    if existing:
        add_album_event_label(label, "Updated from download.")
    else:
        add_album_event_label(label, "Created from download.")

    log(f"\n[DOWNLOAD] Organizing: {artist} - {album} ({year})")
    log(f"  Target: {album_dir}")

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
        
        # Check source file for size warnings (may indicate truncation)
        from tag_operations import check_file_size_warning
        size_warning = check_file_size_warning(src)
        if size_warning:
            level, message = size_warning
            log(f"  [{level}] {src.name}: {message}")
        
        if dest.exists():
            from tag_operations import get_sample_rate, get_audio_duration, get_tags, check_file_size_warning
            
            # First check if existing file is corrupt (can't read tags) or truncated
            # If corrupt, always upgrade (any working file is better than corrupt)
            # If truncated, upgrade if incoming is better (not truncated, or larger if both truncated)
            dest_tags = get_tags(dest)
            dest_is_corrupt = (dest_tags is None)
            dest_size_warning = check_file_size_warning(dest)
            dest_is_truncated = (dest_size_warning is not None)
            
            src_size = src.stat().st_size
            dest_size = dest.stat().st_size
            src_freq = get_sample_rate(src)
            dest_freq = get_sample_rate(dest)
            src_duration = get_audio_duration(src)
            dest_duration = get_audio_duration(dest)
            src_size_warning = check_file_size_warning(src)
            src_is_truncated = (src_size_warning is not None)
            
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
                    log(f"  SKIP: {src.name} (existing file is truncated, but incoming is also truncated and not larger)")
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
                        log(f"  SKIP: {src.name} (existing has higher frequency: {dest_freq}Hz > {src_freq}Hz)")
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
                            log(f"  SKIP: {src.name} (same frequency {src_freq}Hz, existing file is larger or equal: {dest_size} >= {src_size} bytes)")
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
                        log(f"  SKIP: {src.name} (existing file is larger or equal: {dest_size} >= {src_size} bytes)")
            
            if should_move and upgrade_reason:
                freq_str = f" ({src_freq}Hz vs {dest_freq}Hz)" if src_freq and dest_freq else ""
                log(f"  UPGRADE: {src.name}{freq_str} - {', '.join(upgrade_reason)}")

        if should_move:
            log(f"  MOVE: {src} -> {dest}")
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
            # Track destination file for use after moving (for artwork export, etc.)
            dest_items.append((dest, tags_to_use))
            # Track that this file was processed (moved)
            processed_audio_files.append(src)
        else:
            # File was skipped (better version exists) - use existing destination
            dest_items.append((dest, tags_to_use))
            # File was skipped (better version exists) - still mark as processed for cleanup
            processed_audio_files.append(src)

    # Find best art file (standard names + pattern-matched, always largest)
    # This function now handles:
    # - Standard art files (large_cover.jpg, cover.jpg)
    # - Pattern-matched art (e.g., "pure-heroine-lorde.jpg")
    # - Always selects largest by pixel dimensions, then file size
    # IMPORTANT: Always check for pattern-matched art, even if cover.jpg exists,
    # to upgrade if the new art is larger (by pixel dimensions, then file size)
    predownloaded_art = find_predownloaded_art_source_for_album(items)
    used_predownloaded_art = predownloaded_art is not None
    
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
    predownloaded_folder = None
    for d in sorted(root_dirs, key=lambda x: len(str(x))):
        folder_candidate = d / "folder.jpg"
        if folder_candidate.exists():
            predownloaded_folder = folder_candidate
            break
    # Then check child directories
    if not predownloaded_folder:
        for d in sorted(child_dirs, key=lambda x: len(str(x))):
            folder_candidate = d / "folder.jpg"
            if folder_candidate.exists():
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
                if cover_dest.exists():
                    existing_size = get_image_size(cover_dest)
                    if art_size and existing_size:
                        existing_pixels = existing_size[0] * existing_size[1]
                        new_pixels = art_size[0] * art_size[1]
                        if new_pixels <= existing_pixels:
                            should_upgrade = False
                            log(f"  PRE-DOWNLOADED ART: keeping existing cover.jpg (existing: {existing_pixels}px, new: {new_pixels}px - same or smaller dimensions)")
                
                if should_upgrade:
                    if existing_size:
                        log(f"  PRE-DOWNLOADED ART: upgrading cover.jpg with {predownloaded_art.name}{size_str}")
                    else:
                        log(f"  PRE-DOWNLOADED ART: using {predownloaded_art.name}{size_str} for cover.jpg")
                    
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
                                log(f"    Converted {predownloaded_art.suffix} to cover.jpg (optimized)")
                        except Exception as e:
                            log(f"    [WARN] Could not convert {predownloaded_art.name} to JPG, copying as-is: {e}")
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
                                        log(f"    Optimized {predownloaded_art.name} ({src_size} -> {opt_size} bytes, stripped metadata)")
                                except Exception as e:
                                    log(f"    [WARN] Could not optimize {predownloaded_art.name}, copying as-is: {e}")
                                    shutil.copy2(predownloaded_art, cover_dest)
                            else:
                                # Small file, likely already optimized - preserve original
                                shutil.copy2(predownloaded_art, cover_dest)
                                log(f"    Preserved original {predownloaded_art.name} (already optimized)")
                        else:
                            # Non-JPEG format - copy as-is (Roon/T8 can handle PNG)
                            shutil.copy2(predownloaded_art, cover_dest)
                            log(f"    Preserved original format: {predownloaded_art.suffix}")
                    
                    if existing_size:
                        new_pixels = art_size[0] * art_size[1] if art_size else 0
                        old_pixels = existing_size[0] * existing_size[1]
                        log(f"    Upgraded cover.jpg (new: {new_pixels}px, previous: {old_pixels}px)")
                    
                    # Clean up the source art file if it's in the album directory (MUSIC_ROOT)
                    # This handles pattern-matched art files like "pure-heroine-lorde.jpg" that were copied to cover.jpg
                    # Only clean up if the source file is in the same directory as cover.jpg (not in downloads)
                    try:
                        from config import MUSIC_ROOT
                        if predownloaded_art.exists() and album_dir.resolve() in predownloaded_art.resolve().parents:
                            # Source art file is in the album directory - clean it up since we've copied it to cover.jpg
                            log(f"    Cleaning up source art file: {predownloaded_art.name}")
                            if not dry_run:
                                try:
                                    predownloaded_art.unlink()
                                except Exception as e:
                                    log(f"    [WARN] Could not delete source art file {predownloaded_art.name}: {e}")
                    except Exception:
                        # If we can't determine the path relationship, don't clean up (safer)
                        pass
            elif predownloaded_folder:
                # Only folder.jpg exists, use it for cover.jpg
                log(f"  PRE-DOWNLOADED ART: using folder.jpg for cover.jpg")
                shutil.copy2(predownloaded_folder, cover_dest)
            
            # Determine source for folder.jpg:
            # Only create folder.jpg if it doesn't exist
            # If it exists, preserve it (may differ from cover.jpg)
            # If creating, use same as cover.jpg (unless there's a separate predownloaded_folder)
            if not folder_dest.exists():
                if predownloaded_folder and predownloaded_folder != predownloaded_art:
                    # Separate folder.jpg exists in downloads - copy it
                    log(f"  Creating folder.jpg from separate downloads file (may differ from cover.jpg)")
                    shutil.copy2(predownloaded_folder, folder_dest)
                elif predownloaded_art:
                    # Use same art as cover.jpg for folder.jpg
                    if predownloaded_art.suffix.lower() in {".png", ".gif", ".webp"}:
                        # Already converted to cover.jpg above, just copy it
                        shutil.copy2(cover_dest, folder_dest)
                    else:
                        shutil.copy2(predownloaded_art, folder_dest)
                elif predownloaded_folder:
                    # Use folder.jpg for both
                    shutil.copy2(predownloaded_folder, folder_dest)
                elif cover_dest.exists():
                    # No predownloaded art, but cover.jpg exists - use it for folder.jpg
                    shutil.copy2(cover_dest, folder_dest)
            else:
                log(f"  folder.jpg already exists, preserving it (may differ from cover.jpg)")
        
        add_album_event_label(label, "Art found pre-downloaded.")
    else:
        log("  No pre-downloaded art files found.")

    # Use destination files (after moving) for artwork extraction
    # Only use dest_items if files were actually moved (not dry-run)
    # In dry-run mode, files still exist at source paths, so use items_sorted
    files_for_artwork = dest_items if (dest_items and not dry_run) else items_sorted
    ensure_cover_and_folder(
        album_dir,
        files_for_artwork,
        artist,
        album,
        label,
        dry_run=dry_run,
        skip_cover_creation=used_predownloaded_art
    )

    if CLEAN_EMPTY_DOWNLOAD_FOLDERS:
        # Track which artwork files were used/matched for this album
        used_artwork_files = []
        if predownloaded_art:
            used_artwork_files.append(predownloaded_art)
        if predownloaded_folder:
            used_artwork_files.append(predownloaded_folder)
        
        cleanup_download_dirs_for_album(items, dry_run, used_artwork_files, processed_audio_files, extracted_archives or [])


def extract_archives_in_downloads(dry_run: bool = False) -> List[Path]:
    """
    Extract archive files (ZIP, 7z, etc.) found in downloads directory.
    Archives are extracted to a folder with the same name (without extension).
    Extracted files are then processed as normal album folders.
    
    Returns list of extracted archive file paths (for cleanup tracking).
    """
    from config import ARCHIVE_EXTENSIONS
    
    log(f"Scanning for archive files in downloads: {DOWNLOADS_DIR}")
    if not DOWNLOADS_DIR.exists():
        return []
    
    archives_found = []
    for archive_file in DOWNLOADS_DIR.rglob("*"):
        if archive_file.is_file() and archive_file.suffix.lower() in ARCHIVE_EXTENSIONS:
            archives_found.append(archive_file)
    
    if not archives_found:
        return []
    
    log(f"Found {len(archives_found)} archive file(s) to extract.")
    
    extracted_archives = []
    for archive_file in archives_found:
        # Extract to a folder with the same name (without extension)
        extract_dir = archive_file.parent / archive_file.stem
        
        if extract_dir.exists():
            log(f"  [EXTRACT] Skipping {archive_file.name} (extraction folder already exists: {extract_dir.name})")
            # Still track it for cleanup if it was already extracted
            extracted_archives.append(archive_file)
            continue
        
        log(f"  [EXTRACT] Extracting {archive_file.name} to {extract_dir.name}/")
        
        if dry_run:
            log(f"    [DRY RUN] Would extract {archive_file} to {extract_dir}")
            extracted_archives.append(archive_file)
            continue
        
        try:
            import zipfile
            
            if archive_file.suffix.lower() == ".zip":
                extract_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive_file, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                log(f"    Extracted {len(zip_ref.namelist())} file(s) from {archive_file.name}")
                extracted_archives.append(archive_file)
            # Add other archive formats here as needed:
            # elif archive_file.suffix.lower() == ".7z":
            #     # Use py7zr or subprocess to extract 7z files
            #     pass
            else:
                log(f"    [WARN] Unsupported archive format: {archive_file.suffix}")
                continue
                
        except zipfile.BadZipFile:
            log(f"    [WARN] {archive_file.name} is not a valid ZIP file, skipping")
            continue
        except Exception as e:
            log(f"    [WARN] Could not extract {archive_file.name}: {e}")
            continue
    
    return extracted_archives


def process_downloads(dry_run: bool = False) -> None:
    """Process all albums in the downloads directory."""
    from tag_operations import find_audio_files, group_by_album
    
    # First, extract any archive files (ZIP, etc.) found in downloads
    # Track extracted archives for cleanup
    extracted_archives = extract_archives_in_downloads(dry_run)
    
    log(f"Scanning downloads: {DOWNLOADS_DIR}")
    audio_files = list(find_audio_files(DOWNLOADS_DIR))
    if not audio_files:
        log("No audio files found in downloads.")
        return

    albums = group_by_album(audio_files, downloads_root=DOWNLOADS_DIR)
    log(f"Found {len(albums)} album(s) in downloads.")

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
            log(f"\n[WARN] Skipping album {idx}/{len(albums)}: Cannot determine artist/album from tags or path structure")
            log(f"[WARN] Files will remain in downloads folder for manual processing:")
            for file_path in file_paths[:5]:  # Show first 5 files
                log(f"[WARN]   - {file_path}")
            if len(file_paths) > 5:
                log(f"[WARN]   ... and {len(file_paths) - 5} more file(s)")
            log(f"[WARN] Please add tags or organize files in Artist/Album folder structure, then re-run script.")
            from logging_utils import add_global_warning
            add_global_warning(f"Skipped {len(file_paths)} file(s) in downloads - cannot determine artist/album (files: {', '.join([Path(f).name for f in file_paths[:3]])}...)")
            continue

        if year:
            log(f"[DOWNLOAD] Album {idx}/{len(albums)}: {artist} - {album} ({year})")
        else:
            log(f"[DOWNLOAD] Album {idx}/{len(albums)}: {artist} - {album}")

        move_album_from_downloads(album_key, items, MUSIC_ROOT, dry_run, extracted_archives)
    
    if skipped_count > 0:
        log(f"\n[WARN] Skipped {skipped_count} album(s) that could not be reliably determined. Files remain in downloads for manual processing.")
    
    # Match leftover artwork in downloads root to existing albums in library
    match_root_artwork_to_existing_albums(dry_run)
    
    # Final cleanup: Remove cleanup extension files directly in downloads root
    # (ZIP files, partial downloads, etc. that weren't associated with any album)
    if CLEAN_EMPTY_DOWNLOAD_FOLDERS and DOWNLOADS_DIR.exists():
        log(f"\n[CLEANUP] Cleaning up files in downloads root...")
        for f in DOWNLOADS_DIR.iterdir():
            if not f.is_file():
                continue
            
            suffix = f.suffix.lower()
            # Remove files with cleanup extensions (ZIP, partial, etc.)
            # Artwork files are handled separately (preserve if unmatched)
            if suffix in CLEANUP_EXTENSIONS:
                is_artwork_file = suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                if not is_artwork_file:
                    # Remove cleanup extension files (ZIP, partial downloads, etc.)
                    log(f"[CLEANUP] Removing file from downloads root: {f.name}")
                    if not dry_run:
                        try:
                            f.unlink()
                        except Exception as e:
                            log(f"[CLEANUP WARN] Could not delete {f}: {e}")


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
    
    log(f"\n[ROOT ART] Checking {len(root_artwork_files)} artwork file(s) in downloads root for existing albums...")
    
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
        log(f"[ROOT ART] No existing albums found in library to match against.")
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
        
        # Get size info for the root artwork
        root_art_size = get_image_size(art_file)
        if not root_art_size:
            log(f"[ROOT ART] Could not read image size for {art_file.name}, skipping")
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
                    log(f"[ROOT ART] Matched {art_file.name} to {artist} - {album}, but existing artwork is same or better (existing: {existing_pixels}px, root: {root_pixels}px)")
                    # Remove root file since we already have better/same
                    log(f"[ROOT ART] Removing matched artwork from downloads root: {art_file.name}")
                    if not dry_run:
                        try:
                            art_file.unlink()
                            matched_count += 1
                        except Exception as e:
                            log(f"[ROOT ART WARN] Could not delete {art_file.name}: {e}")
                    continue
        
        # Root artwork is better (or no existing artwork) - upgrade
        log(f"[ROOT ART] Matched {art_file.name} to {artist} - {album}, upgrading artwork (root: {root_pixels}px)")
        from logging_utils import album_label_from_dir, add_album_event_label
        label = album_label_from_dir(album_dir)
        add_album_event_label(label, f"Upgraded artwork from downloads root: {art_file.name}")
        
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
                        log(f"[ROOT ART WARN] Could not convert {art_file.name} to JPEG, copying as-is: {e}")
                        shutil.copy2(art_file, cover_path)
                else:
                    shutil.copy2(art_file, cover_path)
                
                # Also update folder.jpg if it doesn't exist or is the same
                folder_path = album_dir / "folder.jpg"
                if not folder_path.exists() or (folder_path.exists() and folder_path.stat().st_size == cover_path.stat().st_size):
                    shutil.copy2(cover_path, folder_path)
                
                # Remove the root artwork file
                art_file.unlink()
                matched_count += 1
            except Exception as e:
                log(f"[ROOT ART WARN] Could not upgrade artwork for {artist} - {album}: {e}")
        else:
            log(f"[ROOT ART] DRY RUN: Would upgrade artwork and remove {art_file.name}")
            matched_count += 1
    
    if matched_count > 0:
        log(f"[ROOT ART] Matched and processed {matched_count} artwork file(s) from downloads root")


def upgrade_albums_to_flac_only(dry_run: bool = False) -> None:
    """
    Enforce FLAC-only where FLAC exists by removing other audio formats.
    BUT: If FLAC files are corrupt (can't read tags) or truncated, remove the FLAC
    and keep the MP3/other format instead, as any incoming file is an "upgrade".
    """
    from logging_utils import add_album_warning_label, album_label_from_dir
    from tag_operations import get_tags, check_file_size_warning
    
    log(f"\n[UPGRADE] Enforcing FLAC-only where FLAC exists...")
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        p = Path(dirpath)
        exts = {Path(name).suffix.lower()
                for name in filenames
                if Path(name).suffix.lower() in AUDIO_EXT}
        if ".flac" not in exts:
            continue

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
                    log(f"  [FLAC CORRUPT] Cannot read tags from {flac_file.name}, will remove FLAC (replacement exists)")
                    corrupt_flacs.append(flac_file)
                else:
                    log(f"  [FLAC CORRUPT] Cannot read tags from {flac_file.name}, keeping it (no replacement found)")
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
                    log(f"  [FLAC TRUNCATED] {flac_file.name}: {message}, will remove FLAC (replacement exists)")
                    corrupt_flacs.append(flac_file)
                else:
                    log(f"  [FLAC TRUNCATED] {flac_file.name}: {message}, keeping it (no replacement found)")
                continue

        # Remove corrupt/truncated FLAC files (only if replacement exists)
        for corrupt_flac in corrupt_flacs:
            log(f"  DELETE (corrupt FLAC, replacement exists): {corrupt_flac}")
            did_cleanup = True
            if not dry_run:
                try:
                    corrupt_flac.unlink()
                except OSError as e:
                    log(f"    [WARN] Could not delete {corrupt_flac}: {e}")
                    label = album_label_from_dir(p)
                    add_album_warning_label(label, f"[WARN] Could not delete corrupt FLAC {corrupt_flac}: {e}")

        # Only remove non-FLAC files if there's a valid FLAC for that specific track
        # Don't remove a non-FLAC file if its corresponding FLAC was just removed (corrupt/truncated)
        valid_flacs = [f for f in flac_files if f not in corrupt_flacs]
        removed_flac_stems = {f.stem for f in corrupt_flacs}  # Track which FLACs were removed
        
        for other_file in other_audio_files:
            # Check if there's a valid FLAC for this specific track (same base name)
            base_name = other_file.stem
            has_valid_flac = any(
                (f.stem == base_name and f not in corrupt_flacs)
                for f in flac_files
            )
            
            # Only remove if there's a valid FLAC for this track
            # Don't remove if the FLAC for this track was just removed (corrupt/truncated)
            if has_valid_flac and base_name not in removed_flac_stems:
                log(f"  DELETE (non-FLAC, valid FLAC exists for this track): {other_file}")
                did_cleanup = True
                if not dry_run:
                    try:
                        other_file.unlink()
                    except OSError as e:
                        log(f"    [WARN] Could not delete {other_file}: {e}")
                        label = album_label_from_dir(p)
                        add_album_warning_label(label, f"[WARN] Could not delete {other_file}: {e}")
            else:
                # Keep the non-FLAC file - either no FLAC exists for this track, or the FLAC was corrupt/removed
                if base_name in removed_flac_stems:
                    log(f"  KEEP (non-FLAC, FLAC for this track was corrupt/removed): {other_file}")
                else:
                    log(f"  KEEP (no valid FLAC for this track): {other_file}")

        if did_cleanup:
            from logging_utils import add_album_event_label
            label = album_label_from_dir(p)
            if corrupt_flacs:
                add_album_event_label(label, f"FLAC-only cleanup (removed {len(corrupt_flacs)} corrupt FLAC(s)).")
            else:
                add_album_event_label(label, "FLAC-only cleanup.")

