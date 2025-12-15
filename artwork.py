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
    """
    mf = MutagenFile(str(first_file))
    if mf is None:
        return False

    if isinstance(mf, FLAC):
        if mf.pictures:
            if not dry_run:
                cover_path.write_bytes(mf.pictures[0].data)
            return True
        return False

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
    Find artist images in the artist folder (e.g., image_medium.png, image_large.png, folder.jpg, artist.jpg).
    Returns the best/largest image found, or None.
    """
    if not artist_dir.exists() or not artist_dir.is_dir():
        return None
    
    # Look for standard artist image filenames (priority order)
    standard_names = ["image_large.png", "image_large.jpg", "image_medium.png", "image_medium.jpg", 
                      "folder.jpg", "artist.jpg", "cover.jpg"]
    
    for name in standard_names:
        candidate = artist_dir / name
        if candidate.exists() and candidate.is_file():
            return candidate
    
    # Also look for any image files in artist folder (might be pattern-matched)
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    candidates = []
    
    for img_file in artist_dir.iterdir():
        if img_file.is_file() and img_file.suffix.lower() in image_extensions:
            # Skip album cover files (they're in subdirectories)
            if img_file.name.lower() not in {"cover.jpg", "folder.jpg"}:
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
    Uses (in order):
      1. Existing images in artist folder (image_large.png, image_medium.png, folder.jpg, artist.jpg, etc.)
      2. Web lookup via MusicBrainz (if enabled)
    
    Creates both folder.jpg and artist.jpg from the best available source.
    """
    if not artist_dir.exists():
        return
    
    folder_path = artist_dir / "folder.jpg"
    artist_path = artist_dir / "artist.jpg"
    
    # Check if we already have both
    if folder_path.exists() and artist_path.exists():
        return
    
    # Find existing artist images in folder
    source_image = find_artist_images_in_folder(artist_dir)
    
    if source_image:
        log(f"  [ARTIST ART] Found existing image: {source_image.name}")
        
        if not dry_run:
            # Convert to JPEG if needed and create both folder.jpg and artist.jpg
            if source_image.suffix.lower() in {".png", ".gif", ".webp"}:
                try:
                    from PIL import Image
                    with Image.open(source_image) as img:
                        # Convert RGBA to RGB if needed
                        if img.mode in ("RGBA", "LA", "P"):
                            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                            if img.mode == "P":
                                img = img.convert("RGBA")
                            rgb_img.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                            img = rgb_img
                        
                        # Save as folder.jpg and artist.jpg
                        img.save(folder_path, "JPEG", quality=95, optimize=True)
                        img.save(artist_path, "JPEG", quality=95, optimize=True)
                        log(f"    Created folder.jpg and artist.jpg from {source_image.name}")
                except Exception as e:
                    log(f"    [WARN] Could not convert {source_image.name} to JPEG: {e}")
                    # Fallback: copy as-is
                    if not folder_path.exists():
                        shutil.copy2(source_image, folder_path)
                    if not artist_path.exists():
                        shutil.copy2(source_image, artist_path)
            else:
                # Already JPEG - optimize only if large, otherwise preserve original
                if not folder_path.exists() or not artist_path.exists():
                    src_size = source_image.stat().st_size
                    # Only optimize if file is unusually large (likely has metadata)
                    if src_size > 1_000_000:  # 1MB threshold
                        try:
                            from PIL import Image
                            with Image.open(source_image) as img:
                                if not folder_path.exists():
                                    img.save(folder_path, "JPEG", quality=95, optimize=True)
                                if not artist_path.exists():
                                    img.save(artist_path, "JPEG", quality=95, optimize=True)
                            opt_size = folder_path.stat().st_size if folder_path.exists() else artist_path.stat().st_size
                            log(f"    Optimized {source_image.name} ({src_size} -> {opt_size} bytes)")
                        except Exception as e:
                            log(f"    [WARN] Could not optimize {source_image.name}, copying as-is: {e}")
                            if not folder_path.exists():
                                shutil.copy2(source_image, folder_path)
                            if not artist_path.exists():
                                shutil.copy2(source_image, artist_path)
                    else:
                        # Small file, preserve original
                        if not folder_path.exists():
                            shutil.copy2(source_image, folder_path)
                        if not artist_path.exists():
                            shutil.copy2(source_image, artist_path)
                        log(f"    Preserved original {source_image.name} (already optimized)")
        return
    
    # Try web lookup if no local image found
    if ENABLE_WEB_ART_LOOKUP:
        log(f"  [ARTIST ART] No local image found, attempting web lookup for {artist}...")
        # For now, web lookup is not fully implemented - would need to use external service
        # fetch_artist_image_from_web(artist, artist_dir, dry_run)
        log(f"  [ARTIST ART] Web lookup not yet implemented (would use MusicBrainz/external service)")


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

    if not skip_cover_creation:
        if not cover_path.exists():
            log("  No cover.jpg; attempting to export embedded art…")
            first_file = album_files[0][0]
            if export_embedded_art_to_cover(first_file, cover_path, dry_run):
                log("  cover.jpg created from embedded art.")
                if label:
                    add_album_event_label(label, "Found art (embedded).")
            else:
                log("  No embedded art; attempting web fetch…")
                if fetch_art_from_web(artist, album, cover_path, dry_run):
                    log("  cover.jpg downloaded from web.")
                    if label:
                        add_album_event_label(label, "Found art (web).")
                else:
                    msg = "[WARN] Could not obtain artwork."
                    log(f"  {msg}")
                    if label:
                        add_album_warning_label(label, msg)
        else:
            log("  cover.jpg already exists.")
    else:
        if cover_path.exists():
            log("  cover.jpg already exists (pre-downloaded art).")
        else:
            log("  (DRY RUN) Skipping cover.jpg creation because pre-downloaded art is found.")

    if cover_path.exists():
        if not folder_path.exists():
            # Create folder.jpg from cover.jpg if it doesn't exist
            # Note: move_album_from_downloads() already handles copying folder.jpg from downloads
            log("  Creating folder.jpg from cover.jpg")
            if not dry_run:
                shutil.copy2(cover_path, folder_path)


def embed_art_into_flacs(album_dir: Path, dry_run: bool = False, backup_enabled: bool = True) -> None:
    """
    Embed cover.jpg into each FLAC in album_dir, backing up FLACs first.
    Used for EMBED_FROM_UPDATES albums (force new art) or EMBED_ALL.
    """
    cover_path = album_dir / "cover.jpg"
    if not cover_path.exists():
        log(f"  [EMBED] No cover.jpg in {album_dir}, skipping.")
        return
    img_data = cover_path.read_bytes()
    for dirpath, dirnames, filenames in os.walk(album_dir):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() == ".flac":
                backup_flac_if_needed(p, dry_run, backup_enabled)
                log(f"  EMBED: updating embedded art in {p}")
                if not dry_run:
                    audio = FLAC(str(p))
                    audio.clear_pictures()
                    pic = Picture()
                    pic.data = img_data
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    pic.desc = "Cover"
                    audio.add_picture(pic)
                    audio.save()


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
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3NoHeaderError
    from mutagen.mp4 import MP4
    
    log("\n[EMBED] Embedding cover.jpg into audio files that have no embedded art...")
    total_checked = 0
    total_embedded = 0

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        album_dir = Path(dirpath)
        cover_path = album_dir / "cover.jpg"
        if not cover_path.exists():
            continue

        label = album_label_from_dir(album_dir)
        cover_data = None
        embedded_any = False

        for name in filenames:
            p = album_dir / name
            if p.suffix.lower() not in AUDIO_EXT:
                continue

            total_checked += 1

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
                                    log(f"  [EMBED] {p.name} already has embedded art, skipping")
                                    break
                    except Exception:
                        pass
                
                # Try MP4/M4A
                if not has_embedded_art and (use_format in {'mp4', 'm4a'} or p.suffix.lower() in {".m4a", ".mp4", ".m4v"}):
                    try:
                        audio = MP4(str(p))
                        if "\xa9cov" in audio:
                            has_embedded_art = True
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
                                log(f"  [EMBED] {p.name} already has embedded art, skipping")
                    except Exception:
                        pass
                        
            except Exception as e:
                log(f"  [EMBED WARN] Could not check embedded art for {p}: {e}")
                # Don't skip - try to embed anyway if we can determine format later

            if has_embedded_art:
                continue

            if cover_data is None:
                try:
                    cover_data = cover_path.read_bytes()
                except Exception as e:
                    log(f"  [EMBED WARN] Could not read cover.jpg in {album_dir}: {e}")
                    if label:
                        add_album_warning_label(label, f"[WARN] Could not read cover.jpg: {e}")
                    break

            log(f"  [EMBED] Embedding art into {p.name} (missing embedded art)")
            embedded_any = True

            if dry_run:
                log(f"    [DRY RUN] Would embed art into {p.name}")
                total_embedded += 1
                continue

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
                            log(f"    [WARN] File has .mp3 extension but is not valid MP3, trying other formats: {e}")
                        else:
                            raise
                
                # Try MP4/M4A if FLAC and MP3 didn't work
                if not embedded and (use_format in {'mp4', 'm4a'} or p.suffix.lower() in {".m4a", ".mp4", ".m4v"}):
                    try:
                        audio = MP4(str(p))
                        audio["\xa9cov"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
                        audio.save()
                        log(f"    ✓ Embedded art into {p.name} (MP4/M4A)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                            log(f"    [WARN] File has MP4 extension but is not valid MP4, trying generic: {e}")
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
                                log(f"  [EMBED WARN] Format {p.suffix} does not support embedded art")
                    except Exception as e:
                        log(f"  [EMBED WARN] Could not embed art using generic method: {e}")
                
                if not embedded:
                    log(f"  [EMBED WARN] Could not determine format or embed art into {p.name}")
            except Exception as e:
                log(f"  [EMBED WARN] Failed to embed art into {p}: {e}")
                if label:
                    add_album_warning_label(label, f"[WARN] Failed to embed art into {p}: {e}")

        if embedded_any and label:
            add_album_event_label(label, "Embedded missing art.")
    
    log(f"[EMBED] Checked {total_checked} files, embedded art into {total_embedded} files")


def fixup_missing_art(dry_run: bool = False) -> None:
    """
    Final pass: scan library for album dirs with audio files but no cover.jpg
    and try to create art (embedded -> web).
    """
    from config import AUDIO_EXT
    from tag_operations import get_tags
    from logging_utils import album_label_from_tags, add_album_event_label, add_album_warning_label
    
    log("\n[ART FIXUP] Scanning library for albums missing cover.jpg...")

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        p = Path(dirpath)
        audio_files = [f for f in filenames if Path(f).suffix.lower() in AUDIO_EXT]
        if not audio_files:
            continue

        cover_path = p / "cover.jpg"
        if cover_path.exists():
            continue

        first_audio_path = p / audio_files[0]
        tags = get_tags(first_audio_path)
        if not tags:
            continue

        artist = tags["artist"]
        album = tags["album"]
        year = tags.get("year", "")
        label = album_label_from_tags(artist, album, year)

        log(f"  [ART FIXUP] Missing cover: {artist} - {album}")

        if export_embedded_art_to_cover(first_audio_path, cover_path, dry_run):
            log("    Extracted embedded art.")
            add_album_event_label(label, "Found missing art (embedded).")
            continue

        if fetch_art_from_web(artist, album, cover_path, dry_run):
            log("    Downloaded cover via web.")
            add_album_event_label(label, "Found missing art (web).")
            continue

        msg = "[WARN] Could not obtain artwork."
        log(f"    {msg}")
        add_album_warning_label(label, msg)

