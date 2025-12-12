"""
Tag operations for reading and processing audio file metadata.
"""
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from mutagen import File as MutagenFile

from config import AUDIO_EXT
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
    If tags cannot be read, falls back to path-based extraction if downloads_root is provided.
    Returns None only if both tag reading and path extraction fail.
    """
    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None or not audio.tags:
            # Try path-based fallback if available
            if downloads_root and downloads_root in path.parents:
                from logging_utils import log
                log(f"[WARN] No tags in {path}, using path-based fallback")
                return get_tags_from_path(path, downloads_root)
            return None
    except Exception as e:
        # File might be corrupted, wrong format, or unreadable
        # Try path-based fallback if available
        from logging_utils import log
        log(f"[WARN] Could not read tags from {path}: {e}")
        if downloads_root and downloads_root in path.parents:
            log(f"  Using path-based fallback for {path}")
            return get_tags_from_path(path, downloads_root)
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


def group_by_album(files: List[Path], downloads_root: Optional[Path] = None) -> Dict[Tuple[str, str], List[Tuple[Path, Dict[str, Any]]]]:
    """
    Group paths into albums by (artist, album) ONLY.
    Year is still read from tags but not used as part of the key.
    If tags can't be read, uses path-based fallback to extract artist/album.
    Returns dict mapping (artist, album) -> list of (path, tags) tuples.
    """
    albums: Dict[Tuple[str, str], List[Tuple[Path, Dict]]] = {}
    
    for f in files:
        tags = get_tags(f, downloads_root)
        if not tags:
            # Last resort: use path fallback even if downloads_root not provided
            from logging_utils import log
            log(f"[WARN] No tags for {f}, using minimal path-based info")
            # Try to infer downloads root from path
            potential_root = f.parent.parent.parent  # Assume Downloads/Music structure
            tags = get_tags_from_path(f, potential_root)
            if not tags:
                log(f"[ERROR] Could not process {f}, skipping.")
                continue

        artist = tags["artist"]
        album = tags["album"]

        key = (artist, album)
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

