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
from logging_utils import album_label_from_dir


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


def fetch_art_from_web(artist: str, album: str, cover_path: Path, dry_run: bool = False) -> Tuple[bool, Optional[str]]:
    """
    Try MusicBrainz + Cover Art Archive with retry logic.
    Returns (True, None) on success, (False, reason) on failure.
    reason is e.g. "no MusicBrainz release", "no front cover in Cover Art Archive", or the last error.
    Note: HTTP 404 from Cover Art Archive means no front cover exists for that release; retrying won't help.
    """
    from structured_logging import logmsg
    if not ENABLE_WEB_ART_LOOKUP:
        return (False, "web art lookup disabled")

    try:
        result = musicbrainzngs.search_releases(
            artist=artist, release=album, limit=1
        )
        releases = result.get("release-list", [])
        if not releases:
            return (False, "no MusicBrainz release")

        mbid = releases[0]["id"]
        url_front = f"https://coverartarchive.org/release/{mbid}/front-500.jpg"

        last_error: Optional[str] = None
        for attempt in range(1, WEB_ART_LOOKUP_RETRIES + 1):
            try:
                r = requests.get(url_front, timeout=WEB_ART_LOOKUP_TIMEOUT)
                if r.status_code == 200:
                    if not dry_run:
                        cover_path.write_bytes(r.content)
                    return (True, None)
                if r.status_code == 404:
                    # No front cover for this release; retrying won't help
                    last_error = "no front cover in Cover Art Archive"
                    logmsg.verbose("Web art: no front cover for release {mbid} (404)", mbid=mbid)
                    break
                last_error = f"HTTP {r.status_code}"
                logmsg.verbose("Web art fetch attempt {attempt} failed: HTTP {status}", attempt=attempt, status=r.status_code)
            except Exception as e:
                last_error = str(e)
                logmsg.verbose("Web art fetch attempt {attempt} failed: {error}", attempt=attempt, error=last_error)

        # If we got 404, try fallback: release may have images but none marked "front"
        if last_error == "no front cover in Cover Art Archive":
            try:
                meta_url = f"https://coverartarchive.org/release/{mbid}"
                rm = requests.get(meta_url, timeout=WEB_ART_LOOKUP_TIMEOUT)
                if rm.status_code == 200:
                    data = rm.json()
                    images = data.get("images", [])
                    if images:
                        # Prefer first image with front=True, else first image
                        img = next((i for i in images if i.get("front")), images[0])
                        img_url = img.get("image") or img.get("thumbnails", {}).get("500")
                        if img_url:
                            r2 = requests.get(img_url, timeout=WEB_ART_LOOKUP_TIMEOUT)
                            if r2.status_code == 200:
                                if not dry_run:
                                    cover_path.write_bytes(r2.content)
                                return (True, None)
            except Exception:
                pass  # Keep last_error as "no front cover..."

        return (False, last_error or "no cover after retries")

    except Exception as e:
        return (False, str(e))


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
        return False
        
    except Exception as e:
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
                
                if should_upgrade:
                    if artist_path.exists() and existing_size:
                        existing_pixels = existing_size[0] * existing_size[1]
                        if dry_run:
                            logmsg.info("Would upgrade artist.jpg (new: {new}px, previous: {prev}px) from {source}", new=best_pixels, prev=existing_pixels, source=source)
                        else:
                            logmsg.info("%item%: Upgrading artist.jpg (new: {new}px, previous: {prev}px) from {source}", new=best_pixels, prev=existing_pixels, source=source)
                    else:
                        if dry_run:
                            logmsg.info("Would create artist.jpg from {source}: {file}", source=source, file=best_image.name)
                        else:
                            logmsg.info("%item%: Creating artist.jpg from {source}: {file}", source=source, file=best_image.name)
                    
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
                                logmsg.info("Cleaned up source file: {file}", file=best_image.name)
                            except Exception as e:
                                logmsg.warn("Could not delete source file {file}: {error}", file=best_image.name, error=str(e))
        
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
                        except Exception as e:
                            logmsg.warn("Could not delete non-standard artist image {file}: {error}", file=img_file.name, error=str(e))
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
        from structured_logging import logmsg
        logmsg.verbose("Backup already exists for %item%, skipping")
        return
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
            item_key = logmsg.begin_item("cover.jpg")
            logmsg.info("folder.jpg exists, creating cover.jpg from it")
            logmsg.end_item(item_key)
            if not dry_run:
                shutil.copy2(folder_path, cover_path)
        return

    # If cover.jpg exists, copy it to folder.jpg (don't overwrite cover.jpg)
    if cover_path.exists():
        if not folder_path.exists():
            item_key = logmsg.begin_item("folder.jpg")
            logmsg.info("cover.jpg exists, creating folder.jpg from it")
            logmsg.end_item(item_key)
            if not dry_run:
                try:
                    shutil.copy2(cover_path, folder_path)
                    logmsg.verbose("folder.jpg created successfully")
                except Exception as e:
                    if label:
                        from structured_logging import logmsg
                        logmsg.warn("Failed to create folder.jpg: {error}", error=str(e))
        return

    # Neither exists - try to create cover.jpg from embedded art or web
    if not skip_cover_creation:
        if not cover_path.exists():
            logmsg.verbose("No cover.jpg; attempting to export embedded art...")
            first_file = album_files[0][0]
            if export_embedded_art_to_cover(first_file, cover_path, dry_run):
                item_key = logmsg.begin_item("cover.jpg")
                logmsg.info("cover.jpg created from embedded art.")
                logmsg.end_item(item_key)
            else:
                logmsg.verbose("No embedded art; attempting web fetch...")
                ok, reason = fetch_art_from_web(artist, album, cover_path, dry_run)
                if ok:
                    item_key = logmsg.begin_item("cover.jpg")
                    logmsg.info("cover.jpg downloaded from web.")
                    logmsg.end_item(item_key)
                else:
                    if reason:
                        logmsg.warn("Could not obtain artwork. ({reason})", reason=reason)
                    else:
                        logmsg.warn("Could not obtain artwork.")
    
    # After creating/finding cover.jpg, ensure folder.jpg exists in album root
    if cover_path.exists() and not folder_path.exists():
        item_key = logmsg.begin_item("folder.jpg")
        logmsg.info("Creating folder.jpg from cover.jpg")
        logmsg.end_item(item_key)
        if not dry_run:
            try:
                shutil.copy2(cover_path, folder_path)
                logmsg.verbose("folder.jpg created successfully")
            except Exception as e:
                from structured_logging import logmsg
                logmsg.warn("Failed to create folder.jpg: {error}", error=str(e))
    
    # Ensure CD1/CD2 subdirectories have folder.jpg (not cover.jpg) if they don't already
    if album_dir.exists():
        source_for_subfolders = None
        if cover_path.exists():
            source_for_subfolders = cover_path
        elif folder_path.exists():
            source_for_subfolders = folder_path
        
        if source_for_subfolders:
            for subdir in album_dir.iterdir():
                if subdir.is_dir() and subdir.name.upper().startswith("CD"):
                    subfolder_folder = subdir / "folder.jpg"
                    if not subfolder_folder.exists():
                        item_key = logmsg.begin_item(f"{subdir.name}/folder.jpg")
                        logmsg.info("Creating folder.jpg in {subdir}/ from album root", subdir=subdir.name)
                        logmsg.end_item(item_key)
                        if not dry_run:
                            try:
                                shutil.copy2(source_for_subfolders, subfolder_folder)
                            except Exception as e:
                                if label:
                                    from structured_logging import logmsg
                                    logmsg.warn("Failed to create folder.jpg in {subdir}/: {error}", subdir=subdir.name, error=str(e))


def ensure_cover_and_folder_global(dry_run: bool = False) -> None:
    """
    For every album directory under MUSIC_ROOT: ensure cover.jpg and folder.jpg
    exist (create from embedded or web if missing, else copy between them).
    Also ensures CD1/CD2 subdirs have folder.jpg from the album root.
    Single place for all cover/folder logic; used instead of per-album ensure in Step 1.
    """
    from config import AUDIO_EXT, MUSIC_ROOT
    from tag_operations import get_tags
    from logging_utils import album_label_from_tags
    from structured_logging import logmsg

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        current = Path(dirpath)
        try:
            rel = current.relative_to(MUSIC_ROOT)
            parts = rel.parts
        except ValueError:
            continue
        if len(parts) != 2:
            continue

        album_dir = current
        audio_files = []
        for r, d, f in os.walk(album_dir):
            for n in f:
                if Path(n).suffix.lower() in AUDIO_EXT:
                    audio_files.append(Path(r) / n)
        audio_files.sort()
        if not audio_files:
            continue

        first_file = audio_files[0]
        tags = get_tags(first_file) or {}
        artist = tags.get("artist", "")
        album = tags.get("album", "")
        year = tags.get("year", "")
        label = album_label_from_tags(artist, album, year)
        album_files = [(first_file, tags)]

        album_key = logmsg.begin_album(album_dir)
        ensure_cover_and_folder(
            album_dir,
            album_files,
            artist,
            album,
            label,
            dry_run=dry_run,
            skip_cover_creation=False,
        )
        logmsg.end_album(album_key)


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
            else:
                logmsg.info("Embedding artwork into %item% (force update)")
                
                embedded = False
                last_error = None
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
                    except Exception as e:
                        last_error = e
                
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
                    except Exception as e:
                        last_error = e
                
                # Try MP4/M4A
                if not embedded and p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                    try:
                        audio = MP4(str(p))
                        cover = MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)
                        audio['covr'] = [cover]
                        audio.save()
                        embedded = True
                    except Exception as e:
                        last_error = e
                
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
                    except Exception as e:
                        last_error = e
                
                if not embedded:
                    err_msg = str(last_error) if last_error else "unknown error"
                    logmsg.warn("Failed to embed artwork into %item%: {error}", error=err_msg)
                else:
                    import run_state
                    run_state.mark_embedded(p)
            
            logmsg.end_item(item_key)
    
    logmsg.end_album(album_key)


def add_missing_tags_global(dry_run: bool = False, backup_enabled: bool = True) -> None:
    """
    Walk the entire MUSIC_ROOT and add missing tags to files that don't have them.
    Also fills in missing albumartist on files that have tags but blank albumartist
    (e.g. Freddie Mercury tracks) so Roon/T8 group correctly.
    Uses structured logging (begin_album, begin_item, info) so the summary shows a count.
    Only writes tags after backing up files (if backup_enabled).
    """
    from config import MUSIC_ROOT, AUDIO_EXT
    from tag_operations import get_tags, write_tags_to_file, choose_album_artist_album
    from pathlib import Path
    from structured_logging import logmsg
    import os
    import re
    
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
        
        # Canonical album-level artist for this directory
        album_level_artist = None
        if files_with_tags:
            album_level_artist, _ = choose_album_artist_album(
                [(f, t) for f, t in files_with_tags], verify_via_mb=False
            )
        
        # Collect (file, action, tags) for every file we will write to
        to_write: List[Tuple[Path, str, Dict[str, Any]]] = []
        
        # Fill missing albumartist on files that already have tags
        if album_level_artist and files_with_tags:
            for audio_file, tags in files_with_tags:
                if not (tags.get("albumartist") or "").strip():
                    to_write.append((audio_file, "albumartist", tags))
        
        # Add full tags to tagless files
        if album_metadata and files_without_tags:
            for audio_file in files_without_tags:
                tracknum = 0
                title = audio_file.stem
                track_match = re.match(r'^(\d+)\s*[-.]\s*', title)
                if track_match:
                    try:
                        tracknum = int(track_match.group(1))
                        title = re.sub(r'^\d+\s*[-.]\s*', '', title).strip()
                    except ValueError:
                        pass
                title = re.sub(r'^[^-]+-\s*', '', title).strip()
                if not title:
                    title = audio_file.stem
                tags_to_write = {
                    "artist": album_metadata["artist"],
                    "album": album_metadata["album"],
                    "year": album_metadata.get("year", ""),
                    "tracknum": tracknum,
                    "discnum": 1,
                    "title": title,
                }
                to_write.append((audio_file, "tags", tags_to_write))
        
        if not to_write:
            continue
        
        # One album we're updating — set album context and log each file for summary count
        artist = album_level_artist or (album_metadata["artist"] if album_metadata else "Unknown Artist")
        album = album_metadata["album"] if album_metadata else "Unknown Album"
        year = album_metadata.get("year", "") if album_metadata else ""
        album_key = logmsg.begin_album(artist, album, year or None)
        
        for audio_file, action, tags in to_write:
            item_key = logmsg.begin_item(audio_file.name)
            if action == "albumartist":
                logmsg.info("Fill albumartist: %item%")
            else:
                logmsg.info("Add tags: %item%")
            if action == "albumartist":
                write_tags_to_file(audio_file, tags, dry_run, backup_enabled, album_artist=album_level_artist)
            else:
                write_tags_to_file(audio_file, tags, dry_run, backup_enabled, album_artist=album_level_artist or (album_metadata["artist"] if album_metadata else None))
            logmsg.end_item(item_key)
        
        logmsg.end_album(album_key)


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
                    except Exception:
                        pass
                        
            except Exception as e:
                logmsg.warn("Could not check embedded art for %item%: {error}", error=str(e))
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
                    logmsg.end_item(item_key)
                    break

            embedded_any = True

            if dry_run:
                logmsg.info("[DRY RUN] Would embed art into %item% (missing embedded art)")
                total_embedded += 1
                logmsg.end_item(item_key)
                continue

            logmsg.info("Embedding art into %item% (missing embedded art)")

            backup_audio_file_if_needed(p, dry_run, backup_enabled)

            # Embed art based on detected or actual file type
            # Try to detect actual format first (handles misnamed files)
            embedded = False
            last_embed_error = None  # Accumulate for single warning at end
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
                        logmsg.info("Embedded art into %item% (FLAC)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() == ".flac":
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            last_embed_error = error_msg
                            logmsg.verbose("File has .flac extension but is not valid FLAC, trying other formats: {error}", error=error_msg)
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
                        logmsg.info("Embedded art into %item% (MP3)")
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
                        logmsg.info("Embedded art into %item% (MP3)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() == ".mp3":
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            last_embed_error = error_msg
                            logmsg.verbose("File has .mp3 extension but is not valid MP3, trying other formats: {error}", error=error_msg)
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
                        logmsg.info("Embedded art into %item% (MP4/M4A)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            last_embed_error = error_msg
                            logmsg.verbose("Could not embed art into MP4/M4A file %item%: {error}", error=error_msg)
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
                                logmsg.info("Embedded art into %item% (generic)")
                                total_embedded += 1
                                embedded = True
                            else:
                                logmsg.warn("Format {ext} does not support embedded art", ext=p.suffix)
                    except Exception as e:
                        error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                        if len(error_msg) > 200:
                            error_msg = error_msg[:197] + "..."
                        last_embed_error = error_msg
                        logmsg.verbose("Could not embed art using generic method: {error}", error=error_msg)
                
                if not embedded:
                    if last_embed_error:
                        logmsg.warn("Could not embed art into %item%: {error}", error=last_embed_error)
                    else:
                        logmsg.warn("Could not determine format or embed art into %item%")
                else:
                    import run_state
                    run_state.mark_embedded(p)
            except Exception as e:
                # Don't log the exception object directly (may contain binary data)
                error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                # Truncate very long error messages
                if len(error_msg) > 200:
                    error_msg = error_msg[:197] + "..."
                logmsg.warn("Failed to embed art into %item%: {error}", error=error_msg)
            
            logmsg.end_item(item_key)

        # Events tracked automatically by structured logging
        
        logmsg.end_album(album_key)
    
