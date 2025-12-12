"""
Tag operations for reading and processing audio file metadata.
"""
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from mutagen import File as MutagenFile
import musicbrainzngs

from config import AUDIO_EXT, ENABLE_WEB_ART_LOOKUP, MB_APP, MB_VER, MB_CONTACT, WEB_ART_LOOKUP_TIMEOUT
from logging_utils import log


def find_audio_files(root: Path) -> Iterator[Path]:
    """Generator that yields all audio files under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in AUDIO_EXT:
                yield p


def get_tags_from_path(path: Path, downloads_root: Path) -> Dict[str, Any]:
    """
    Fallback: Extract basic info from file path when tags can't be read.
    Assumes structure like: downloads_root/Artist/Album/track.flac
    """
    try:
        rel = path.relative_to(downloads_root)
        parts = list(rel.parts)
        
        # Extract artist and album from path
        if len(parts) >= 2:
            artist = parts[0]
            album = parts[1]
        elif len(parts) == 1:
            artist = "Unknown Artist"
            album = "Unknown Album"
        else:
            artist = "Unknown Artist"
            album = "Unknown Album"
        
        # Extract title from filename (remove extension and track number if present)
        title = path.stem
        # Try to remove leading track number like "02 - " or "02."
        import re
        title = re.sub(r'^\d+\s*[-.]\s*', '', title).strip()
        if not title:
            title = path.stem
        
        return {
            "artist": artist.strip(),
            "album": album.strip(),
            "year": "",
            "tracknum": 0,
            "discnum": 1,
            "title": title.strip(),
        }
    except Exception:
        # Fallback to minimal info
        return {
            "artist": "Unknown Artist",
            "album": "Unknown Album",
            "year": "",
            "tracknum": 0,
            "discnum": 1,
            "title": path.stem,
        }


def get_tags(path: Path, downloads_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Return tags dict from a file: artist, album, year, tracknum, discnum, title.
    Returns None if tags cannot be read. Does NOT use path-based fallback here -
    that decision is made at the directory level in group_by_album().
    """
    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None or not audio.tags:
            return None
    except Exception as e:
        # File might be corrupted, wrong format, or unreadable
        # Log warning but return None - path-based fallback will be handled at directory level
        from logging_utils import log
        log(f"[WARN] Could not read tags from {path}: {e}")
        return None

    try:
        def _get(tag: str, default: str = "") -> str:
            v = audio.tags.get(tag)
            return v[0] if v else default

        artist = _get("albumartist") or _get("artist") or "Unknown Artist"
        album = _get("album") or "Unknown Album"

        date = _get("date") or _get("year") or ""
        year = date[:4] if len(date) >= 4 and date[:4].isdigit() else ""

        trackno = _get("tracknumber") or "0"
        discno = _get("discnumber") or "1"
        title = _get("title") or path.stem

        try:
            tracknum = int(trackno.split("/")[0])
        except ValueError:
            tracknum = 0

        try:
            discnum = int(discno.split("/")[0])
        except ValueError:
            discnum = 1

        return {
            "artist": artist.strip(),
            "album": album.strip(),
            "year": year.strip(),
            "tracknum": tracknum,
            "discnum": discnum,
            "title": title.strip(),
        }
    except Exception as e:
        # Error reading tags even though file opened
        from logging_utils import log
        log(f"[WARN] Error processing tags from {path}: {e}")
        return None


def verify_album_via_musicbrainz(artist: str, album: str) -> Optional[Tuple[str, str]]:
    """
    Query MusicBrainz to verify/identify an album.
    Returns (verified_artist, verified_album) if found, None otherwise.
    Handles "Various Artists" compilations.
    """
    if not ENABLE_WEB_ART_LOOKUP:
        return None
    
    try:
        # Initialize MusicBrainz if not already done
        try:
            musicbrainzngs.set_useragent(MB_APP, MB_VER, MB_CONTACT)
        except Exception:
            pass  # Already initialized
        
        # Search for the release
        result = musicbrainzngs.search_releases(
            artist=artist,
            release=album,
            limit=5  # Get a few results to find best match
        )
        
        releases = result.get("release-list", [])
        if not releases:
            return None
        
        # Use the first result (most relevant)
        release = releases[0]
        
        # Get artist credit (handles "Various Artists" and collaborations)
        artist_credit_list = release.get("artist-credit", [])
        if artist_credit_list:
            # For compilations, artist-credit might be empty or have "Various Artists"
            # Check if it's a compilation
            if release.get("release-group", {}).get("secondary-type-list"):
                secondary_types = release["release-group"]["secondary-type-list"]
                if any(st.get("secondary-type") == "Compilation" for st in secondary_types):
                    verified_artist = "Various Artists"
                else:
                    # Get primary artist from credit
                    artist_name = artist_credit_list[0].get("name", artist)
                    verified_artist = artist_name
            else:
                # Regular album - get primary artist
                artist_name = artist_credit_list[0].get("name", artist)
                verified_artist = artist_name
        else:
            # No artist credit - might be compilation
            verified_artist = "Various Artists"
        
        verified_album = release.get("title", album)
        
        return (verified_artist, verified_album)
        
    except Exception as e:
        log(f"  [WARN] MusicBrainz lookup failed for {artist} - {album}: {e}")
        return None


def choose_album_artist_album(items: List[Tuple[Path, Dict[str, Any]]], verify_via_mb: bool = True) -> Tuple[str, str]:
    """
    Given a list of (path, tags) for files in the same directory, pick canonical
    artist and album values similar to choose_album_year.
    
    Strategy:
      1. Collect all artist/album pairs from files that have tags.
      2. Find the most common (artist, album) pair.
      3. If tags exist, use them directly (they already handle Various Artists correctly).
      4. If can't determine albumDir from most used tag (all tags are missing):
         - Use path-based fallback to extract artist/album from folder structure
         - Verify via MusicBrainz (for Various Artists detection and verification)
      5. Last resort: use path-based fallback or "Unknown Artist/Album".
    
    Returns (artist, album) tuple.
    """
    # Collect artist/album from files with tags
    artist_album_pairs = [(t["artist"], t["album"]) for (_p, t) in items if t.get("artist") and t.get("album")]
    
    if artist_album_pairs:
        # Find most common (artist, album) pair
        # Tags already handle Various Artists correctly, so use them as-is
        counts = Counter(artist_album_pairs)
        max_count = max(counts.values())
        candidates = [pair for pair, c in counts.items() if c == max_count]
        candidate_artist, candidate_album = candidates[0]
        
        # Use tag values directly (they already handle Various Artists)
        return (candidate_artist, candidate_album)
    
    # Can't determine albumDir from most used tag (all tags are missing)
    # Use path-based fallback, then verify via MusicBrainz (for Various Artists detection)
    if items:
        first_path = items[0][0]
        fallback_tags = get_tags_from_path(first_path, first_path.parent.parent.parent)
        if fallback_tags:
            path_artist = fallback_tags["artist"]
            path_album = fallback_tags["album"]
            
            # Try MusicBrainz verification before using path-based values
            # This is where we detect Various Artists when tags don't exist
            if verify_via_mb and path_artist != "Unknown Artist" and path_album != "Unknown Album":
                verified = verify_album_via_musicbrainz(path_artist, path_album)
                if verified:
                    verified_artist, verified_album = verified
                    log(f"  [MB VERIFY] Verified path-based: {path_artist} - {path_album} -> {verified_artist} - {verified_album}")
                    return (verified_artist, verified_album)
                else:
                    log(f"  [MB VERIFY] No MusicBrainz match for path-based {path_artist} - {path_album}, using path values")
            
            return (path_artist, path_album)
    
    # Last resort
    return ("Unknown Artist", "Unknown Album")


def group_by_album(files: List[Path], downloads_root: Optional[Path] = None) -> Dict[Tuple[str, str], List[Tuple[Path, Dict[str, Any]]]]:
    """
    Group paths into albums by (artist, album) ONLY.
    Year is still read from tags but not used as part of the key.
    
    Strategy:
      1. First, group files by their parent directory (album folder in downloads)
      2. For each directory group, determine artist/album from files with tags
         (using most common value, similar to choose_album_year)
      3. If can't determine from tags (all tags missing), use path-based fallback
         and verify via MusicBrainz (for Various Artists detection)
      4. For files without tags, use the determined artist/album
      5. Group all files by the determined (artist, album) key
    
    Returns dict mapping (artist, album) -> list of (path, tags) tuples.
    """
    # Step 1: Group files by their parent directory
    files_by_dir: Dict[Path, List[Path]] = {}
    for f in files:
        parent_dir = f.parent
        files_by_dir.setdefault(parent_dir, []).append(f)
    
    # Step 2: For each directory, get tags and determine artist/album
    all_items: List[Tuple[Path, Dict[str, Any]]] = []
    dir_to_key: Dict[Path, Tuple[str, str]] = {}
    
    for dir_path, dir_files in files_by_dir.items():
        # Get tags for all files in this directory
        items_with_tags: List[Tuple[Path, Dict[str, Any]]] = []
        items_without_tags: List[Path] = []
        
        for f in dir_files:
            tags = get_tags(f, downloads_root)
            if tags:
                items_with_tags.append((f, tags))
            else:
                items_without_tags.append(f)
        
        # Determine artist/album from files with tags (with MusicBrainz verification)
        if items_with_tags:
            artist, album = choose_album_artist_album(items_with_tags, verify_via_mb=True)
            dir_to_key[dir_path] = (artist, album)
            
            # Add files with tags
            all_items.extend(items_with_tags)
            
            # For files without tags, create minimal tags using determined artist/album
            for f in items_without_tags:
                from logging_utils import log
                log(f"[WARN] No tags for {f}, using artist/album from other files in directory: {artist} - {album}")
                # Create minimal tags with determined artist/album
                fallback_tags = get_tags_from_path(f, downloads_root if downloads_root else f.parent.parent.parent)
                if fallback_tags:
                    fallback_tags["artist"] = artist
                    fallback_tags["album"] = album
                    all_items.append((f, fallback_tags))
                else:
                    # Last resort
                    all_items.append((f, {
                        "artist": artist,
                        "album": album,
                        "year": "",
                        "tracknum": 0,
                        "discnum": 1,
                        "title": f.stem,
                    }))
        else:
            # Can't determine albumDir from most used tag (all tags are missing)
            # Use path-based fallback, then verify via MusicBrainz
            if dir_files:
                first_file = dir_files[0]
                fallback_tags = get_tags_from_path(first_file, downloads_root if downloads_root else first_file.parent.parent.parent)
                if fallback_tags:
                    path_artist = fallback_tags["artist"]
                    path_album = fallback_tags["album"]
                    
                    # Verify via MusicBrainz before using path-based values
                    verified = verify_album_via_musicbrainz(path_artist, path_album)
                    if verified:
                        artist, album = verified
                        log(f"[WARN] No tags in directory {dir_path}, MusicBrainz verified: {path_artist} - {path_album} -> {artist} - {album}")
                    else:
                        artist, album = path_artist, path_album
                        log(f"[WARN] No tags in directory {dir_path}, using path-based: {artist} - {album}")
                    
                    dir_to_key[dir_path] = (artist, album)
                    
                    for f in dir_files:
                        tags = get_tags_from_path(f, downloads_root if downloads_root else f.parent.parent.parent)
                        if tags:
                            tags["artist"] = artist
                            tags["album"] = album
                            all_items.append((f, tags))
    
    # Step 3: Group all items by (artist, album) key
    albums: Dict[Tuple[str, str], List[Tuple[Path, Dict]]] = {}
    for f, tags in all_items:
        # Use the determined key for this file's directory, or fall back to tags
        dir_path = f.parent
        if dir_path in dir_to_key:
            key = dir_to_key[dir_path]
        else:
            key = (tags["artist"], tags["album"])
        
        albums.setdefault(key, []).append((f, tags))
    
    return albums


def choose_album_year(items: List[Tuple[Path, Dict[str, Any]]]) -> str:
    """
    Given a list of (path, tags) for an album, pick a canonical year
    to use in the folder name.

    Strategy:
      - Collect all non-empty year strings from tags["year"].
      - If none, return "" (no year in folder).
      - Otherwise:
          * Find the most common year.
          * If there's a tie, pick the earliest year numerically.
    """
    years = [t["year"] for (_p, t) in items if t.get("year")]
    if not years:
        return ""

    counts = Counter(years)
    max_count = max(counts.values())
    candidates = [y for y, c in counts.items() if c == max_count]

    numeric_candidates = []
    for y in candidates:
        try:
            numeric_candidates.append((int(y[:4]), y))
        except ValueError:
            numeric_candidates.append((9999, y))

    numeric_candidates.sort(key=lambda x: (x[0], x[1]))
    return numeric_candidates[0][1]


def sanitize_filename_component(name: str) -> str:
    """
    Make a string safe for use as a Windows/macOS filename component:
    - Replace invalid characters: <>:"/\\|?*
    - Strip trailing spaces and periods (Windows hates those)
    """
    invalid = '<>:"/\\|?*'
    sanitized = "".join("_" if c in invalid else c for c in name)
    # Windows: no trailing space or dot
    sanitized = sanitized.rstrip(" .")
    return sanitized


def format_track_filename(tags: Dict[str, Any], ext: str) -> str:
    """Format a track filename from tags."""
    safe_title = sanitize_filename_component(tags["title"])
    return f"{tags['tracknum']:02d} - {safe_title}{ext.lower()}"

