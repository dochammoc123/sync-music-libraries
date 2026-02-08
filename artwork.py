"""
Artwork handling: embedding, fetching, and managing album artwork.
"""
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import musicbrainzngs
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, ID3NoHeaderError, APIC
from mutagen.mp4 import MP4, MP4Cover
from mutagen import File as MutagenFile
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from config import (
    BACKUP_ROOT,
    ENABLE_WEB_ART_LOOKUP,
    MB_APP,
    MB_CONTACT,
    MB_VER,
    MUSIC_ROOT,
    WEB_ART_LOOKUP_RETRIES,
    WEB_ART_LOOKUP_TIMEOUT,
)
from logging_utils import (
    add_album_event_label,
    add_album_warning_label,
    album_label_from_dir,
    log,
)


def init_musicbrainz() -> None:
    """Initialize MusicBrainz user agent."""
    musicbrainzngs.set_useragent(MB_APP, MB_VER, MB_CONTACT)


def export_embedded_art_to_cover(first_file: Path, cover_path: Path, dry_run: bool = False) -> bool:
    """
    Export embedded artwork from the first audio file to cover.jpg.
    Returns True if successful, False otherwise.
    Supports FLAC, MP3, and MP4/M4A formats.
    """
    mf = MutagenFile(str(first_file))
    if mf is None:
        return False

    # FLAC files
    if isinstance(mf, FLAC):
        if mf.pictures:
            if not dry_run:
                cover_path.write_bytes(mf.pictures[0].data)
            return True
        return False

    # MP4/M4A files
    if isinstance(mf, MP4):
        try:
            # MP4 files store artwork in the 'covr' atom
            if 'covr' in mf:
                # Get the first cover image
                cover = mf['covr'][0]
                if isinstance(cover, MP4Cover):
                    if not dry_run:
                        cover_path.write_bytes(cover)
                    return True
        except Exception as e:
            # Log but don't fail - try other methods
            log(f"    [ART WARN] Could not extract MP4 cover: {e}")
            pass

    # MP3 files (ID3/APIC)
    try:
        id3 = ID3(str(first_file))
        pics = [f for f in id3.values() if isinstance(f, APIC)]
        if pics:
            if not dry_run:
                cover_path.write_bytes(pics[0].data)
            return True
    except Exception:
        pass

    return False


def fetch_art_from_web(artist: str, album: str, cover_path: Path, dry_run: bool = False) -> bool:
    """
    Try MusicBrainz + Cover Art Archive with retry logic.
    Returns True on success, False otherwise.
    """
    if not ENABLE_WEB_ART_LOOKUP:
        return False

    try:
        result = musicbrainzngs.search_releases(
            artist=artist, release=album, limit=1
        )
        releases = result.get("release-list", [])
        if not releases:
            return False

        mbid = releases[0]["id"]
        url = f"https://coverartarchive.org/release/{mbid}/front-500.jpg"

        for attempt in range(1, WEB_ART_LOOKUP_RETRIES + 1):
            try:
                log(f"    [WEB] Fetch attempt {attempt}/{WEB_ART_LOOKUP_RETRIES}...")
                r = requests.get(url, timeout=WEB_ART_LOOKUP_TIMEOUT)
                if r.status_code == 200:
                    if not dry_run:
                        cover_path.write_bytes(r.content)
                    return True
            except Exception as e:
                log(f"    [WEB WARN] Attempt {attempt} failed: {e}")

        return False

    except Exception as e:
        log(f"  [WARN] Art lookup failed for {artist} - {album}: {e}")
        return False


def find_artist_images_in_folder(artist_dir: Path) -> Optional[Path]:
    """
    Find artist images in the artist folder.
    Priority order:
      1. folder.jpg (preferred for artist art)
      2. artist.jpg (secondary standard name)
      3. Any other image files (normalized - any name/type)
    
    Returns the best/largest image found, or None.
    """
    if not artist_dir.exists() or not artist_dir.is_dir():
        return None
    
    # Priority 1: Check for folder.jpg (preferred for artist art)
    folder_jpg = artist_dir / "folder.jpg"
    if folder_jpg.exists() and folder_jpg.is_file():
        return folder_jpg
    
    # Priority 2: Check for artist.jpg (secondary standard name)
    artist_jpg = artist_dir / "artist.jpg"
    if artist_jpg.exists() and artist_jpg.is_file():
        return artist_jpg
    
    # Priority 3: Look for any image files in artist folder (normalized - any name/type)
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    candidates = []
    
    for img_file in artist_dir.iterdir():
        if img_file.is_file() and img_file.suffix.lower() in image_extensions:
            # Skip standard album cover files (cover.jpg is for albums, not artists)
            # But folder.jpg and artist.jpg are already checked above
            if img_file.name.lower() != "cover.jpg":
                size_info = get_image_size(img_file)
                if size_info:
                    candidates.append((img_file, size_info))
    
    # Return largest by pixel dimensions
    if candidates:
        candidates.sort(key=lambda x: (x[1][0] * x[1][1], x[1][2]), reverse=True)
        return candidates[0][0]
    
    return None


def fetch_artist_image_from_web(artist: str, artist_dir: Path, dry_run: bool = False) -> bool:
    """
    Try to fetch artist image from MusicBrainz or other sources.
    MusicBrainz artist images are available via their API.
    Returns True on success, False otherwise.
    """
    if not ENABLE_WEB_ART_LOOKUP:
        return False
    
    try:
        init_musicbrainz()
        
        # Search for artist in MusicBrainz
        result = musicbrainzngs.search_artists(artist=artist, limit=1)
        artists = result.get("artist-list", [])
        if not artists:
            return False
        
        artist_mbid = artists[0]["id"]
        
        # MusicBrainz doesn't directly provide artist images, but we can try:
        # 1. Check if there's a relationship to an image resource
        # 2. Use external services that provide artist images based on MBID
        
        # For now, try a common pattern (this may need adjustment based on actual API)
        # Some services use: https://musicbrainz.org/ws/2/artist/{mbid}?inc=url-rels
        # Then look for image URLs in relationships
        
        # Alternative: Use Last.fm or other services that provide artist images
        # Last.fm API: http://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={artist}&api_key={key}
        
        # For now, return False - we'll need to implement based on available services
        log(f"  [ARTIST ART] MusicBrainz found artist {artist} (MBID: {artist_mbid}), but artist image fetching not yet implemented")
        return False
        
    except Exception as e:
        log(f"  [WARN] Artist image lookup failed for {artist}: {e}")
        return False


def ensure_artist_images(artist_dir: Path, artist: str, dry_run: bool = False) -> None:
    """
    Ensure folder.jpg and artist.jpg exist in the artist folder.
    
    Logic:
      - If folder.jpg exists, copy it to artist.jpg (if missing) - don't overwrite folder.jpg
      - If artist.jpg exists, copy it to folder.jpg (if missing) - don't overwrite artist.jpg
      - If neither exists, search for sources and create both:
        1. Existing images in artist folder (MUSIC_ROOT/Artist/)
        2. Artist images from downloads folder (DOWNLOADS_DIR/Artist/)
        3. Artist images from overlay folder (UPDATE_ROOT/Artist/)
      - Select best/largest image and create both folder.jpg and artist.jpg
      - Convert/normalize any image file found (any name/type)
      - No web lookup (don't add missing artist art)
    """
    from config import DOWNLOADS_DIR, UPDATE_ROOT, MUSIC_ROOT
    from structured_logging import logmsg
    
    if not artist_dir.exists():
        return
    
    folder_path = artist_dir / "folder.jpg"
    artist_path = artist_dir / "artist.jpg"
    
    # Set artist context (using artist name as context)
    item_key = logmsg.begin_item(artist)
    
    try:
        # Track if we need to ensure the files exist
        need_to_ensure = True
        
        # If both exist, nothing to do (but still clean up non-standard files)
        if folder_path.exists() and artist_path.exists():
            logmsg.verbose("Both folder.jpg and artist.jpg exist, skipping")
            need_to_ensure = False
        
        # If folder.jpg exists, copy it to artist.jpg (don't overwrite folder.jpg)
        if folder_path.exists():
            if not artist_path.exists():
                if dry_run:
                    logmsg.info("Would create artist.jpg from folder.jpg")
                else:
                    logmsg.info("Creating artist.jpg from folder.jpg")
                log(f"  [ARTIST ART] folder.jpg exists, creating artist.jpg from it")
                if not dry_run:
                    shutil.copy2(folder_path, artist_path)
            need_to_ensure = False
        
        # If artist.jpg exists, copy it to folder.jpg (don't overwrite artist.jpg)
        if artist_path.exists():
            if not folder_path.exists():
                if dry_run:
                    logmsg.info("Would create folder.jpg from artist.jpg")
                else:
                    logmsg.info("%item%: Creating folder.jpg from artist.jpg")
                log(f"  [ARTIST ART] artist.jpg exists, creating folder.jpg from it")
                if not dry_run:
                    shutil.copy2(artist_path, folder_path)
            need_to_ensure = False
        
        # Find best artist image from multiple sources (only if we need to ensure files exist)
        if need_to_ensure:
            candidates = []
            
            # 1. Check existing images in artist folder (MUSIC_ROOT/Artist/)
            source_image = find_artist_images_in_folder(artist_dir)
            if source_image:
                size_info = get_image_size(source_image)
                if size_info:
                    candidates.append((source_image, size_info, "existing"))
            
            # 2. Check downloads artist folder (DOWNLOADS_DIR/Artist/)
            downloads_artist_dir = DOWNLOADS_DIR / artist_dir.name if DOWNLOADS_DIR.exists() else None
            if downloads_artist_dir and downloads_artist_dir.exists():
                downloads_image = find_artist_images_in_folder(downloads_artist_dir)
                if downloads_image:
                    size_info = get_image_size(downloads_image)
                    if size_info:
                        candidates.append((downloads_image, size_info, "downloads"))
            
            # 3. Check overlay artist folder (UPDATE_ROOT/Artist/)
            overlay_artist_dir = None
            if UPDATE_ROOT.exists():
                try:
                    rel = artist_dir.relative_to(MUSIC_ROOT)
                    overlay_artist_dir = UPDATE_ROOT / rel
                except ValueError:
                    # artist_dir is not under MUSIC_ROOT, skip overlay check
                    pass
            if overlay_artist_dir and overlay_artist_dir.exists():
                overlay_image = find_artist_images_in_folder(overlay_artist_dir)
                if overlay_image:
                    size_info = get_image_size(overlay_image)
                    if size_info:
                        candidates.append((overlay_image, size_info, "overlay"))
            
            
            # Select best (largest by pixel dimensions)
            if candidates:
            candidates.sort(key=lambda x: (x[1][0] * x[1][1], x[1][2]), reverse=True)
            best_image, best_size, source = candidates[0]
            best_pixels = best_size[0] * best_size[1]
            
            # Check if we should upgrade existing artist.jpg
            should_upgrade = True
            existing_size = None
            if artist_path.exists():
                existing_size = get_image_size(artist_path)
                if existing_size:
                    existing_pixels = existing_size[0] * existing_size[1]
                    if best_pixels <= existing_pixels:
                        should_upgrade = False
                        logmsg.verbose("Keeping existing artist.jpg (existing: {existing}px, new: {new}px - same or smaller dimensions)", existing=existing_pixels, new=best_pixels)
                        log(f"  [ARTIST ART] Keeping existing artist.jpg (existing: {existing_pixels}px, new: {best_pixels}px - same or smaller dimensions)")
            
            if should_upgrade:
                if artist_path.exists() and existing_size:
                    existing_pixels = existing_size[0] * existing_size[1]
                    if dry_run:
                        logmsg.info("Would upgrade artist.jpg (new: {new}px, previous: {prev}px) from {source}", new=best_pixels, prev=existing_pixels, source=source)
                    else:
                        logmsg.info("%item%: Upgrading artist.jpg (new: {new}px, previous: {prev}px) from {source}", new=best_pixels, prev=existing_pixels, source=source)
                    log(f"  [ARTIST ART] Found image in {source}: {best_image.name} ({best_size[0]}x{best_size[1]}, {best_size[2]} bytes)")
                    log(f"    Upgrading artist.jpg (new: {best_pixels}px, previous: {existing_pixels}px)")
                else:
                    if dry_run:
                        logmsg.info("Would create artist.jpg from {source}: {file}", source=source, file=best_image.name)
                    else:
                        logmsg.info("%item%: Creating artist.jpg from {source}: {file}", source=source, file=best_image.name)
                    log(f"  [ARTIST ART] Found image in {source}: {best_image.name} ({best_size[0]}x{best_size[1]}, {best_size[2]} bytes)")
                    log(f"    Creating artist.jpg from {best_image.name}")
                
                if not dry_run:
                    # Convert to JPEG if needed
                    if best_image.suffix.lower() in {".png", ".gif", ".webp"}:
                        try:
                            from PIL import Image
                            with Image.open(best_image) as img:
                                # Convert RGBA to RGB if needed
                                if img.mode in ("RGBA", "LA", "P"):
                                    rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                                    if img.mode == "P":
                                        img = img.convert("RGBA")
                                    rgb_img.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                                    img = rgb_img
                                artist_path.parent.mkdir(parents=True, exist_ok=True)
                                img.save(artist_path, "JPEG", quality=95, optimize=True)
                        except Exception as e:
                            logmsg.warn("Could not convert {file} to JPEG: {error}", file=best_image.name, error=str(e))
                            log(f"    [WARN] Could not convert {best_image.name} to JPEG: {e}")
                            artist_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(best_image, artist_path)
                    else:
                        # Already JPEG - copy it
                        artist_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(best_image, artist_path)
                    
                    # Also create folder.jpg from same source (use artist.jpg, not cover.jpg)
                    if not folder_path.exists():
                        logmsg.verbose("Creating folder.jpg from artist.jpg")
                        shutil.copy2(artist_path, folder_path)
                    
                    # Clean up source file if it's from downloads or overlay (not from existing artist folder)
                    if source in ("downloads", "overlay"):
                        try:
                            best_image.unlink()
                            logmsg.verbose("Cleaned up source file: {file}", file=best_image.name)
                            log(f"    Cleaned up source file: {best_image.name}")
                        except Exception as e:
                            logmsg.warn("Could not delete source file {file}: {error}", file=best_image.name, error=str(e))
                            log(f"    [WARN] Could not delete source file {best_image.name}: {e}")
        
        # Clean up non-standard artist image files (anything that's not artist.jpg or folder.jpg)
        # This ensures only the standard files are synced to T8 in Step 9
        # Only clean up if both standard files exist (either they already existed or we just created them)
        if folder_path.exists() and artist_path.exists():
            image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            standard_names = {"artist.jpg", "folder.jpg"}
            
            for img_file in artist_dir.iterdir():
                if img_file.is_file() and img_file.suffix.lower() in image_extensions:
                    # Skip standard files and cover.jpg (album art, not artist art)
                    if img_file.name.lower() not in standard_names and img_file.name.lower() != "cover.jpg":
                        try:
                            if not dry_run:
                                img_file.unlink()
                            logmsg.verbose("Removing non-standard artist image: {file}", file=img_file.name)
                            log(f"    [ARTIST ART] Removing non-standard artist image: {img_file.name}")
                        except Exception as e:
                            logmsg.warn("Could not delete non-standard artist image {file}: {error}", file=img_file.name, error=str(e))
                            log(f"    [WARN] Could not delete non-standard artist image {img_file.name}: {e}")
    finally:
        # Always unset item context, even if we return early or encounter an exception
        logmsg.end_item(item_key)
    
    # No artist images found - don't try web lookup (user preference: don't add missing)


def normalize_for_filename(text: str) -> str:
    """
    Normalize text for filename matching (e.g., "Pure Heroine" -> "pure-heroine").
    Converts to lowercase, replaces spaces/special chars with hyphens, removes extra hyphens.
    """
    # Convert to lowercase
    normalized = text.lower()
    # Replace spaces and common separators with hyphens
    normalized = re.sub(r'[\s_]+', '-', normalized)
    # Remove special characters except hyphens
    normalized = re.sub(r'[^a-z0-9\-]', '', normalized)
    # Remove multiple consecutive hyphens
    normalized = re.sub(r'-+', '-', normalized)
    # Remove leading/trailing hyphens
    normalized = normalized.strip('-')
    return normalized


def get_image_size(image_path: Path) -> Optional[Tuple[int, int, int]]:
    """
    Get image dimensions (width, height) and file size.
    Returns (width, height, file_size_bytes) or None if can't read.
    """
    try:
        if HAS_PIL:
            with Image.open(image_path) as img:
                width, height = img.size
                file_size = image_path.stat().st_size
                return (width, height, file_size)
        else:
            # Fallback: just use file size if PIL not available
            file_size = image_path.stat().st_size
            return (0, 0, file_size)
    except Exception:
        return None


def find_art_by_pattern(artist: str, album: str, search_dirs: List[Path]) -> List[Tuple[Path, Tuple[int, int, int]]]:
    """
    Find artwork files that match artist/album pattern (e.g., "pure-heroine-lorde.jpg").
    Returns list of (path, (width, height, file_size)) tuples, sorted by size (largest first).
    """
    if not artist or not album:
        return []
    
    # Normalize artist and album for matching
    norm_artist = normalize_for_filename(artist)
    norm_album = normalize_for_filename(album)
    
    # Pattern: album-artist.ext or artist-album.ext (with variations)
    patterns = [
        f"{norm_album}-{norm_artist}",  # "pure-heroine-lorde"
        f"{norm_artist}-{norm_album}",  # "lorde-pure-heroine"
    ]
    
    # Also try with common variations (50th-anniversary, etc. might be in filename)
    # We'll match if filename contains both normalized album and artist
    found_art = []
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        
        for art_file in search_dir.iterdir():
            if not art_file.is_file():
                continue
            
            if art_file.suffix.lower() not in image_extensions:
                continue
            
            # Skip standard art filenames (handled separately)
            if art_file.name.lower() in {"large_cover.jpg", "cover.jpg", "folder.jpg"}:
                continue
            
            # Check if filename matches pattern
            stem_lower = art_file.stem.lower()
            matches = False
            
            # Try exact pattern matches first
            for pattern in patterns:
                if pattern in stem_lower:
                    matches = True
                    break
            
            # Also check if filename contains both normalized album and artist
            if not matches:
                if norm_album in stem_lower and norm_artist in stem_lower:
                    matches = True
            
            if matches:
                size_info = get_image_size(art_file)
                if size_info:
                    found_art.append((art_file, size_info))
    
    # Sort by pixel dimensions (width * height), then by file size
    found_art.sort(key=lambda x: (x[1][0] * x[1][1], x[1][2]), reverse=True)
    return found_art


def find_predownloaded_art_source_for_album(items: List[Tuple[Path, Dict[str, Any]]]) -> Optional[Path]:
    """
    Given the list of (path, tags) for an album's tracks in DOWNLOADS_DIR,
    look in their directories for artwork files.
    
    Strategy:
      1. Find standard art files: large_cover.jpg > cover.jpg
      2. Find pattern-matched art files (e.g., "pure-heroine-lorde.jpg") by matching artist/album tags
      3. Also check DOWNLOADS_DIR itself for artwork (for browser downloads)
      4. Always select the largest image (by pixel dimensions, then file size)
      5. Prioritize root directories over subdirectories
    
    Returns the best art file Path or None.
    """
    from tag_operations import find_root_album_directory, choose_album_artist_album
    from config import DOWNLOADS_DIR
    
    # Get artist/album from tags
    items_with_tags = [(p, t) for (p, t) in items if t.get("artist") and t.get("album")]
    if items_with_tags:
        artist, album = choose_album_artist_album(items_with_tags, verify_via_mb=False)
    else:
        # No tags, can't match by pattern
        artist, album = None, None
    
    # Find root album directories (treating subdirectories as part of parent)
    all_files = [p for (p, _tags) in items]
    root_dirs = set()
    child_dirs = set()
    
    for p, _tags in items:
        root_dir = find_root_album_directory(p, all_files, DOWNLOADS_DIR)
        root_dirs.add(root_dir)
        if p.parent != root_dir:
            child_dirs.add(p.parent)
    
    # Also check DOWNLOADS_DIR itself (for browser downloads)
    search_dirs = list(root_dirs) + list(child_dirs)
    if DOWNLOADS_DIR.exists() and DOWNLOADS_DIR not in root_dirs:
        search_dirs.append(DOWNLOADS_DIR)
    
    # Collect all candidate art files with their sizes
    candidates: List[Tuple[Path, Tuple[int, int, int]]] = []
    
    # 1. Check for standard art files (large_cover.jpg, cover.jpg)
    art_priority = ["large_cover.jpg", "cover.jpg"]
    for art_name in art_priority:
        for d in sorted(root_dirs, key=lambda x: len(str(x))):
            candidate = d / art_name
            if candidate.exists():
                size_info = get_image_size(candidate)
                if size_info:
                    candidates.append((candidate, size_info))
                    break  # Found in root, don't check child dirs for this name
    
        # Check child directories if not found in root
        if not any(c.name.lower() == art_name.lower() for c, _ in candidates):
            for d in sorted(child_dirs, key=lambda x: len(str(x))):
                candidate = d / art_name
                if candidate.exists():
                    size_info = get_image_size(candidate)
                    if size_info:
                        candidates.append((candidate, size_info))
                        break
    
    # 2. Find pattern-matched art files (e.g., "pure-heroine-lorde.jpg")
    if artist and album:
        pattern_art = find_art_by_pattern(artist, album, search_dirs)
        candidates.extend(pattern_art)
    
    # 3. Select the best (largest by pixel dimensions, then file size)
    if candidates:
        # Already sorted by size in find_art_by_pattern, but re-sort all candidates
        candidates.sort(key=lambda x: (x[1][0] * x[1][1], x[1][2]), reverse=True)
        best_art = candidates[0][0]
        best_size = candidates[0][1]
        log(f"  [ART] Selected best art: {best_art.name} ({best_size[0]}x{best_size[1]}, {best_size[2]} bytes)")
        return best_art
    
    return None


def backup_audio_file_if_needed(audio_path: Path, dry_run: bool = False, backup_enabled: bool = True) -> None:
    """
    If backup_enabled is True, create a backup copy of this audio file under BACKUP_ROOT,
    mirroring MUSIC_ROOT structure. Only create if it does not already exist.
    Works for all audio file types (FLAC, MP3, M4A, etc.), not just FLAC.
    """
    if not backup_enabled:
        return
    try:
        rel = audio_path.relative_to(MUSIC_ROOT)
    except ValueError:
        return
    backup_path = BACKUP_ROOT / rel
    if backup_path.exists():
        # Backup already exists - skip to avoid overwriting original backup
        # This handles cases where file is modified multiple times (tags, then art)
        return
    log(f"  BACKUP: {audio_path} -> {backup_path}")
    if not dry_run:
        import shutil
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(audio_path, backup_path)


# Alias for backward compatibility
def backup_flac_if_needed(flac_path: Path, dry_run: bool = False, backup_enabled: bool = True) -> None:
    """Alias for backup_audio_file_if_needed() for backward compatibility."""
    backup_audio_file_if_needed(flac_path, dry_run, backup_enabled)


def ensure_cover_and_folder(
    album_dir: Path,
    album_files: List[Tuple[Path, Dict[str, Any]]],
    artist: str,
    album: str,
    label: Optional[str],
    dry_run: bool = False,
    skip_cover_creation: bool = False
) -> None:
    """
    Ensure cover.jpg and folder.jpg exist, using (in order):
      - Standard pre-downloaded art (if already copied),
      - embedded art from first track,
      - web lookup via MusicBrainz.
    """
    import shutil
    
    cover_path = album_dir / "cover.jpg"
    folder_path = album_dir / "folder.jpg"

    # If both exist, nothing to do
    if cover_path.exists() and folder_path.exists():
        return

    from structured_logging import logmsg
    
    # If folder.jpg exists, copy it to cover.jpg (don't overwrite folder.jpg)
    if folder_path.exists():
        if not cover_path.exists():
            logmsg.verbose("folder.jpg exists, creating cover.jpg from it")
            log("  folder.jpg exists, creating cover.jpg from it")
            if not dry_run:
                shutil.copy2(folder_path, cover_path)
        return

    # If cover.jpg exists, copy it to folder.jpg (don't overwrite cover.jpg)
    if cover_path.exists():
        if not folder_path.exists():
            logmsg.verbose("cover.jpg exists, creating folder.jpg from it")
            log("  cover.jpg exists, creating folder.jpg from it")
            if not dry_run:
                try:
                    shutil.copy2(cover_path, folder_path)
                    logmsg.verbose("folder.jpg created successfully")
                    log("  ✓ folder.jpg created successfully")
                except Exception as e:
                    log(f"  [WARN] Failed to create folder.jpg: {e}")
                    if label:
                        add_album_warning_label(label, f"Failed to create folder.jpg: {e}")
        return

    # Neither exists - try to create cover.jpg from embedded art or web
    if not skip_cover_creation:
        if not cover_path.exists():
            logmsg.verbose("No cover.jpg; attempting to export embedded art…")
            log("  No cover.jpg; attempting to export embedded art…")
            first_file = album_files[0][0]
            if export_embedded_art_to_cover(first_file, cover_path, dry_run):
                logmsg.verbose("cover.jpg created from embedded art.")
                log("  cover.jpg created from embedded art.")
                if label:
                    add_album_event_label(label, "Found art (embedded).")
            else:
                logmsg.verbose("No embedded art; attempting web fetch…")
                log("  No embedded art; attempting web fetch…")
                if fetch_art_from_web(artist, album, cover_path, dry_run):
                    logmsg.verbose("cover.jpg downloaded from web.")
                    log("  cover.jpg downloaded from web.")
                    if label:
                        add_album_event_label(label, "Found art (web).")
                else:
                    msg = "[WARN] Could not obtain artwork."
                    log(f"  {msg}")
                    if label:
                        add_album_warning_label(label, msg)
                        logmsg.warn("Could not obtain artwork.")
    
    # After creating/finding cover.jpg, ensure folder.jpg exists in album root
    if cover_path.exists() and not folder_path.exists():
        logmsg.verbose("Creating folder.jpg from cover.jpg")
        log("  Creating folder.jpg from cover.jpg")
        if not dry_run:
            try:
                shutil.copy2(cover_path, folder_path)
                logmsg.verbose("folder.jpg created successfully")
                log("  ✓ folder.jpg created successfully")
            except Exception as e:
                log(f"  [WARN] Failed to create folder.jpg: {e}")
                if label:
                    add_album_warning_label(label, f"Failed to create folder.jpg: {e}")
    
    # Ensure CD1/CD2 subdirectories have folder.jpg (not cover.jpg) if they don't already
    # Use album root cover.jpg or folder.jpg as source
    if album_dir.exists():
        source_for_subfolders = None
        if cover_path.exists():
            source_for_subfolders = cover_path
        elif folder_path.exists():
            source_for_subfolders = folder_path
        
        if source_for_subfolders:
            # Check for CD1, CD2, etc. subdirectories
            for subdir in album_dir.iterdir():
                if subdir.is_dir() and subdir.name.upper().startswith("CD"):
                    subfolder_folder = subdir / "folder.jpg"
                    if not subfolder_folder.exists():
                        logmsg.verbose("Creating folder.jpg in {subdir}/ from album root", subdir=subdir.name)
                        log(f"  Creating folder.jpg in {subdir.name}/ from album root")
                        if not dry_run:
                            try:
                                shutil.copy2(source_for_subfolders, subfolder_folder)
                                logmsg.verbose("folder.jpg created in {subdir}/", subdir=subdir.name)
                                log(f"  ✓ folder.jpg created in {subdir.name}/")
                            except Exception as e:
                                log(f"  [WARN] Failed to create folder.jpg in {subdir.name}/: {e}")
                                if label:
                                    add_album_warning_label(label, f"Failed to create folder.jpg in {subdir.name}/: {e}")


def embed_art_into_audio_files(album_dir: Path, dry_run: bool = False, backup_enabled: bool = True) -> None:
    """
    Embed cover.jpg into each audio file in album_dir, backing up files first.
    Used for EMBED_FROM_UPDATES albums (force new art) or EMBED_ALL.
    Supports FLAC, MP3, MP4/M4A, and other formats.
    """
    from structured_logging import logmsg
    from logging_utils import album_label_from_dir
    from config import AUDIO_EXT
    from mutagen import File as MutagenFile
    from mutagen.mp3 import MP3
    
    cover_path = album_dir / "cover.jpg"
    if not cover_path.exists():
        log(f"  [EMBED] No cover.jpg in {album_dir}, skipping.")
        return
    
    # Set album context for structured logging
    album_key = logmsg.begin_album(album_dir)
    
    img_data = cover_path.read_bytes()
    for dirpath, dirnames, filenames in os.walk(album_dir):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() not in AUDIO_EXT:
                continue
            
            item_key = logmsg.begin_item(p.name)
            backup_audio_file_if_needed(p, dry_run, backup_enabled)
            
            if dry_run:
                logmsg.info("Would embed artwork into %item% (force update)")
                log(f"  EMBED: would update embedded art in {p}")
            else:
                logmsg.info("Embedding artwork into %item% (force update)")
                log(f"  EMBED: updating embedded art in {p}")
                
                embedded = False
                # Try FLAC first
                if p.suffix.lower() == ".flac":
                    try:
                        audio = FLAC(str(p))
                        audio.clear_pictures()
                        pic = Picture()
                        pic.data = img_data
                        pic.type = 3
                        pic.mime = "image/jpeg"
                        pic.desc = "Cover"
                        audio.add_picture(pic)
                        audio.save()
                        embedded = True
                    except Exception:
                        pass
                
                # Try MP3
                if not embedded and p.suffix.lower() == ".mp3":
                    try:
                        audio = MP3(str(p))
                        if audio.tags is None:
                            audio.add_tags()
                        # Remove existing APIC frames
                        audio.tags.delall("APIC")
                        audio.tags.add(APIC(
                            encoding=3,  # UTF-8
                            mime="image/jpeg",
                            type=3,  # Cover (front)
                            desc="Cover",
                            data=img_data
                        ))
                        audio.save()
                        embedded = True
                    except Exception:
                        pass
                
                # Try MP4/M4A
                if not embedded and p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                    try:
                        audio = MP4(str(p))
                        cover = MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)
                        audio['covr'] = [cover]
                        audio.save()
                        embedded = True
                    except Exception:
                        pass
                
                # Try generic MutagenFile for other formats
                if not embedded:
                    try:
                        audio = MutagenFile(str(p))
                        if audio is not None and hasattr(audio, "pictures"):
                            audio.clear_pictures()
                            pic = Picture()
                            pic.data = img_data
                            pic.type = 3
                            pic.mime = "image/jpeg"
                            pic.desc = "Cover"
                            audio.add_picture(pic)
                            audio.save()
                            embedded = True
                    except Exception:
                        pass
            
            logmsg.end_item(item_key)
    
    logmsg.end_album(album_key)


def add_missing_tags_global(dry_run: bool = False, backup_enabled: bool = True) -> None:
    """
    Walk the entire MUSIC_ROOT and add missing tags to files that don't have them.
    Only writes tags after backing up files (if backup_enabled).
    Uses album metadata from other files in the same album directory.
    """
    from config import MUSIC_ROOT, AUDIO_EXT
    from tag_operations import get_tags, write_tags_to_file
    from pathlib import Path
    import os
    
    log("\n[ADD TAGS] Adding missing tags to files without tags...")
    total_processed = 0
    total_added = 0
    
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        album_dir = Path(dirpath)
        audio_files = [album_dir / f for f in filenames if Path(f).suffix.lower() in AUDIO_EXT]
        if not audio_files:
            continue
        
        # Get album metadata from files that have tags
        album_metadata = None
        files_with_tags = []
        files_without_tags = []
        
        for audio_file in audio_files:
            tags = get_tags(audio_file)
            # Consider file to have tags if it has artist and album (tracknum and title can be missing)
            if tags and tags.get("artist") and tags.get("album"):
                files_with_tags.append((audio_file, tags))
                if not album_metadata:
                    album_metadata = {
                        "artist": tags.get("artist"),
                        "album": tags.get("album"),
                        "year": tags.get("year", ""),
                    }
            else:
                files_without_tags.append(audio_file)
        
        total_processed += len(audio_files)
        
        # If we have album metadata and files without tags, add tags to them
        if album_metadata and files_without_tags:
            log(f"  [ADD TAGS] Found {len(files_without_tags)} file(s) without tags in {album_dir.name}")
            for audio_file in files_without_tags:
                # Extract track number and title from filename
                import re
                tracknum = 0
                title = audio_file.stem
                
                # Extract track number from filename like "02 - " or "02."
                track_match = re.match(r'^(\d+)\s*[-.]\s*', title)
                if track_match:
                    try:
                        tracknum = int(track_match.group(1))
                        # Remove track number prefix
                        title = re.sub(r'^\d+\s*[-.]\s*', '', title).strip()
                    except ValueError:
                        pass
                
                # Remove artist prefix if present (e.g., "Lorde - 400 Lux" -> "400 Lux")
                title = re.sub(r'^[^-]+-\s*', '', title).strip()
                if not title:
                    title = audio_file.stem
                
                # Build complete tags using album metadata
                tags_to_write = {
                    "artist": album_metadata["artist"],
                    "album": album_metadata["album"],
                    "year": album_metadata.get("year", ""),
                    "tracknum": tracknum,
                    "discnum": 1,
                    "title": title,
                }
                
                log(f"  [ADD TAGS] Adding tags to {audio_file.name}: {tags_to_write}")
                if write_tags_to_file(audio_file, tags_to_write, dry_run, backup_enabled):
                    log(f"    ✓ Added tags to {audio_file.name}")
                    total_added += 1
                else:
                    log(f"    [WARN] Could not add tags to {audio_file.name}")
    
    log(f"[ADD TAGS] Processed {total_processed} files, added tags to {total_added} files")


def embed_missing_art_global(dry_run: bool = False, backup_enabled: bool = True, embed_if_missing: bool = True) -> None:
    """
    Walk the entire MUSIC_ROOT and embed cover.jpg into audio files
    that currently have no embedded artwork.
    Works with FLAC, MP3, MP4/M4A, and other formats.
    """
    if not embed_if_missing:
        return
    
    from config import AUDIO_EXT
    from logging_utils import album_label_from_dir
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3NoHeaderError
    from mutagen.mp4 import MP4
    from structured_logging import logmsg
    
    total_checked = 0
    total_embedded = 0

    # Track processed albums to avoid duplicates (e.g., CD1 and CD2 subdirectories)
    processed_albums = set()
    
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        current_dir = Path(dirpath)
        
        # Determine the parent album directory (for multi-disc albums with CD1/CD2 subdirectories)
        # Check if we're in a subdirectory (CD1, CD2, etc.)
        try:
            rel = current_dir.relative_to(MUSIC_ROOT)
            parts = list(rel.parts)
            # If we're in a subdirectory (CD1, CD2, etc.), use parent as album directory
            if len(parts) > 2 and (parts[-1].upper().startswith("CD") or len(parts) > 3):
                parent_album_dir = current_dir.parent
            else:
                parent_album_dir = current_dir
        except ValueError:
            # Path is not under MUSIC_ROOT, use current directory
            parent_album_dir = current_dir
        
        # Check for cover.jpg in the parent album directory (not subdirectories)
        cover_path = parent_album_dir / "cover.jpg"
        if not cover_path.exists():
            continue
        
        # Determine if we're in a subdirectory (CD1, CD2, etc.)
        is_subdirectory = (current_dir != parent_album_dir)
        
        # Use parent album directory for album context
        album_key = logmsg.begin_album(parent_album_dir)
        album_label = album_label_from_dir(parent_album_dir)
        
        # Only skip if we've already processed the parent album directory itself
        # (not subdirectories - we want to process files in both parent and subdirectories)
        if not is_subdirectory and album_label in processed_albums:
            logmsg.end_album(album_key)
            continue
        
        # Mark parent album directory as processed (only once, when we first encounter it)
        if not is_subdirectory:
            processed_albums.add(album_label)
        
        cover_data = None
        embedded_any = False

        for name in filenames:
            p = current_dir / name
            if p.suffix.lower() not in AUDIO_EXT:
                continue

            total_checked += 1
            item_key = logmsg.begin_item(p.name)

            # Check if file already has embedded art
            # Try to detect actual format (not just extension) to handle misnamed files
            has_embedded_art = False
            detected_format = None
            
            try:
                # First, try to detect actual format
                audio_test = MutagenFile(str(p))
                if audio_test is not None:
                    # Detect format from MutagenFile type
                    class_name = type(audio_test).__name__.lower()
                    if 'flac' in class_name:
                        detected_format = 'flac'
                    elif 'mp3' in class_name or 'id3' in class_name:
                        detected_format = 'mp3'
                    elif 'mp4' in class_name or 'm4a' in class_name:
                        detected_format = 'mp4'
            except Exception:
                pass  # Will try format-specific handlers below
            
            # Use detected format if available, otherwise fall back to extension
            use_format = detected_format or p.suffix.lower().lstrip('.')
            
            try:
                # Try FLAC first
                if use_format == 'flac' or p.suffix.lower() == ".flac":
                    try:
                        audio = FLAC(str(p))
                        if len(audio.pictures) > 0:
                            has_embedded_art = True
                            logmsg.verbose("%item% already has embedded art, skipping")
                            log(f"  [EMBED] {p.name} already has embedded art, skipping")
                    except Exception:
                        # Not actually FLAC, try other formats
                        pass
                
                # Try MP3
                if not has_embedded_art and (use_format == 'mp3' or p.suffix.lower() == ".mp3"):
                    try:
                        from mutagen.mp3 import MP3
                        audio = MP3(str(p))
                        if audio.tags:
                            # Check for APIC frames (cover art)
                            for key in audio.tags.keys():
                                if key.startswith("APIC"):
                                    has_embedded_art = True
                                    logmsg.verbose("%item% already has embedded art, skipping")
                                    log(f"  [EMBED] {p.name} already has embedded art, skipping")
                                    break
                    except Exception:
                        pass
                
                # Check MP4/M4A for embedded art
                if not has_embedded_art and (use_format == 'mp4' or p.suffix.lower() in {".m4a", ".mp4", ".m4v"}):
                    try:
                        audio = MP4(str(p))
                        if 'covr' in audio:
                            has_embedded_art = True
                            logmsg.verbose("%item% already has embedded art, skipping")
                            log(f"  [EMBED] {p.name} already has embedded art, skipping")
                    except Exception:
                        pass
                
                # Generic check for other formats
                if not has_embedded_art:
                    try:
                        audio = MutagenFile(str(p))
                        if audio is not None:
                            if hasattr(audio, "pictures") and len(audio.pictures) > 0:
                                has_embedded_art = True
                                logmsg.verbose("%item% already has embedded art, skipping")
                                log(f"  [EMBED] {p.name} already has embedded art, skipping")
                    except Exception:
                        pass
                        
            except Exception as e:
                logmsg.warn("Could not check embedded art for %item%: {error}", error=str(e))
                log(f"  [EMBED WARN] Could not check embedded art for {p}: {e}")
                # Don't skip - try to embed anyway if we can determine format later

            if has_embedded_art:
                logmsg.end_item(item_key)
                continue

            # Check if MP4/M4A already has embedded art
            if p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                try:
                    audio = MP4(str(p))
                    if 'covr' in audio:
                        has_embedded_art = True
                        logmsg.verbose("%item% already has embedded art, skipping")
                        log(f"  [EMBED] {p.name} already has embedded art, skipping")
                except Exception:
                    pass  # Will try to embed below
                
                if has_embedded_art:
                    logmsg.end_item(item_key)
                    continue

            if cover_data is None:
                try:
                    cover_data = cover_path.read_bytes()
                except Exception as e:
                    logmsg.warn("Could not read cover.jpg: {error}", error=str(e))
                    log(f"  [EMBED WARN] Could not read cover.jpg in {album_dir}: {e}")
                    logmsg.end_item(item_key)
                    break

            embedded_any = True

            if dry_run:
                logmsg.info("[DRY RUN] Would embed art into %item% (missing embedded art)")
                log(f"  [EMBED] [DRY RUN] Would embed art into {p.name} (missing embedded art)")
                total_embedded += 1
                logmsg.end_item(item_key)
                continue

            logmsg.info("Embedding art into %item% (missing embedded art)")
            log(f"  [EMBED] Embedding art into {p.name} (missing embedded art)")

            backup_audio_file_if_needed(p, dry_run, backup_enabled)

            # Embed art based on detected or actual file type
            # Try to detect actual format first (handles misnamed files)
            embedded = False
            try:
                # Try FLAC first (if extension or detected format suggests it)
                if use_format == 'flac' or p.suffix.lower() == ".flac":
                    try:
                        audio = FLAC(str(p))
                        pic = Picture()
                        pic.data = cover_data
                        pic.type = 3
                        pic.mime = "image/jpeg"
                        pic.desc = "Cover"
                        audio.clear_pictures()
                        audio.add_picture(pic)
                        audio.save()
                        log(f"    ✓ Embedded art into {p.name} (FLAC)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() == ".flac":
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            logmsg.warn("File has .flac extension but is not valid FLAC, trying other formats: {error}", error=error_msg)
                            log(f"    [WARN] File has .flac extension but is not valid FLAC, trying other formats: {e}")
                        else:
                            raise
                
                # Try MP3 if FLAC didn't work
                if not embedded and (use_format == 'mp3' or p.suffix.lower() == ".mp3"):
                    try:
                        from mutagen.mp3 import MP3
                        audio = MP3(str(p))
                        if audio.tags is None:
                            audio.add_tags()
                        audio.tags.add(APIC(
                            encoding=3,  # UTF-8
                            mime="image/jpeg",
                            type=3,  # Cover (front)
                            desc="Cover",
                            data=cover_data
                        ))
                        audio.save()
                        log(f"    ✓ Embedded art into {p.name} (MP3)")
                        total_embedded += 1
                        embedded = True
                    except ID3NoHeaderError:
                        # File has no ID3 tags, add them
                        audio = MP3(str(p))
                        audio.add_tags()
                        audio.tags.add(APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=cover_data
                        ))
                        audio.save()
                        log(f"    ✓ Embedded art into {p.name} (MP3)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() == ".mp3":
                            # Don't log the exception object directly (may contain binary data)
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            # Truncate very long error messages
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            logmsg.warn("File has .mp3 extension but is not valid MP3, trying other formats: {error}", error=error_msg)
                            log(f"    [WARN] File has .mp3 extension but is not valid MP3, trying other formats: {error_msg}")
                        else:
                            raise
                
                # Try MP4/M4A if not already embedded
                if not embedded and (use_format == 'mp4' or p.suffix.lower() in {".m4a", ".mp4", ".m4v"}):
                    try:
                        audio = MP4(str(p))
                        # MP4 files store artwork in the 'covr' atom
                        # Create MP4Cover object with JPEG data
                        cover = MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)
                        audio['covr'] = [cover]
                        audio.save()
                        log(f"    ✓ Embedded art into {p.name} (MP4/M4A)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                            # Don't log the exception object directly (may contain binary data)
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            logmsg.warn("Could not embed art into MP4/M4A file %item%: {error}", error=error_msg)
                            log(f"    [WARN] Could not embed art into {p.name} (MP4/M4A): {e}")
                        else:
                            raise
                
                # Try generic MutagenFile for other formats
                if not embedded:
                    try:
                        audio = MutagenFile(str(p))
                        if audio is not None:
                            # Try to add art (format-specific)
                            if hasattr(audio, "add_picture"):
                                pic = Picture()
                                pic.data = cover_data
                                pic.type = 3
                                pic.mime = "image/jpeg"
                                pic.desc = "Cover"
                                audio.add_picture(pic)
                                audio.save()
                                log(f"    ✓ Embedded art into {p.name} (generic)")
                                total_embedded += 1
                                embedded = True
                            else:
                                logmsg.warn("Format {ext} does not support embedded art", ext=p.suffix)
                                log(f"  [EMBED WARN] Format {p.suffix} does not support embedded art")
                    except Exception as e:
                        # Don't log the exception object directly (may contain binary data)
                        error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                        # Truncate very long error messages
                        if len(error_msg) > 200:
                            error_msg = error_msg[:197] + "..."
                        logmsg.warn("Could not embed art using generic method: {error}", error=error_msg)
                        log(f"  [EMBED WARN] Could not embed art using generic method: {error_msg}")
                
                if not embedded:
                    logmsg.warn("Could not determine format or embed art into %item%")
                    log(f"  [EMBED WARN] Could not determine format or embed art into {p.name}")
            except Exception as e:
                # Don't log the exception object directly (may contain binary data)
                error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                # Truncate very long error messages
                if len(error_msg) > 200:
                    error_msg = error_msg[:197] + "..."
                logmsg.warn("Failed to embed art into %item%: {error}", error=error_msg)
                log(f"  [EMBED WARN] Failed to embed art into {p}: {error_msg}")
            
            logmsg.end_item(item_key)

        if embedded_any:
            from logging_utils import add_album_event_label
            add_album_event_label(album_label, "Embedded missing art.")
        
        logmsg.end_album(album_key)
    
    log(f"[EMBED] Checked {total_checked} files, embedded art into {total_embedded} files")


def fixup_missing_art(dry_run: bool = False) -> None:
    """
    Final pass: scan library for album dirs with audio files but no cover.jpg
    and try to create art (embedded -> web).
    
    Handles multi-disc albums (CD1, CD2, etc.) by checking the parent album directory
    for cover.jpg, not just the subdirectory.
    """
    from config import AUDIO_EXT, MUSIC_ROOT
    from tag_operations import get_tags
    from logging_utils import album_label_from_tags, add_album_event_label, add_album_warning_label
    from structured_logging import logmsg
    
    log("\n[ART FIXUP] Scanning library for albums missing cover.jpg...")
    
    # Track processed albums to avoid duplicates (e.g., CD1 and CD2 subdirectories)
    processed_albums = set()

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        p = Path(dirpath)
        audio_files = [f for f in filenames if Path(f).suffix.lower() in AUDIO_EXT]
        if not audio_files:
            continue

        first_audio_path = p / audio_files[0]
        tags = get_tags(first_audio_path)
        if not tags:
            continue

        artist = tags["artist"]
        album = tags["album"]
        year = tags.get("year", "")
        label = album_label_from_tags(artist, album, year)
        
        # Skip if we've already processed this album (e.g., from CD1 subdirectory)
        if label in processed_albums:
            continue
        
        # Determine the album directory (parent if we're in a CD1/CD2 subdirectory)
        # Walk up to find the album directory (should be 2 levels deep: Artist/Album or Artist/(Year) Album)
        album_dir = p
        try:
            rel = p.relative_to(MUSIC_ROOT)
            parts = list(rel.parts)
            # If we're in a subdirectory (CD1, CD2, etc.), use parent as album directory
            if len(parts) > 2:
                # Check if last part looks like a disc subdirectory
                if parts[-1].upper().startswith("CD") or len(parts) > 3:
                    album_dir = p.parent
        except ValueError:
            # Path is not under MUSIC_ROOT, use current directory
            pass

        cover_path = album_dir / "cover.jpg"
        if cover_path.exists():
            continue

        # Mark this album as processed to avoid duplicates
        processed_albums.add(label)

        # Set album context
        album_key = logmsg.begin_album(album_dir)

        log(f"  [ART FIXUP] Missing cover: {artist} - {album}")

        if export_embedded_art_to_cover(first_audio_path, cover_path, dry_run):
            # Set item context to the created cover file (more interesting than source FLAC)
            item_key = logmsg.begin_item(cover_path.name)
            logmsg.info("Extracted embedded art to %item%")
            log("    Extracted embedded art.")
            logmsg.end_item(item_key)
            add_album_event_label(label, "Found missing art (embedded).")
            logmsg.end_album(album_key)
            continue

        if fetch_art_from_web(artist, album, cover_path, dry_run):
            logmsg.info("Downloaded cover via web")
            log("    Downloaded cover via web.")
            add_album_event_label(label, "Found missing art (web).")
            logmsg.end_album(album_key)
            continue

        logmsg.warn("Could not obtain artwork")
        msg = "[WARN] Could not obtain artwork."
        log(f"    {msg}")
        add_album_warning_label(label, msg)
        logmsg.end_album(album_key)

