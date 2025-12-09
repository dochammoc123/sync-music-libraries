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


def get_tags(path: Path) -> Optional[Dict[str, Any]]:
    """
    Return tags dict from a file: artist, album, year, tracknum, discnum, title.
    Returns None if tags cannot be read or file is invalid/corrupted.
    """
    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None or not audio.tags:
            return None
    except Exception as e:
        # File might be corrupted, wrong format, or unreadable
        # Log warning but don't crash - just skip this file
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


def group_by_album(files: List[Path]) -> Dict[Tuple[str, str], List[Tuple[Path, Dict[str, Any]]]]:
    """
    Group paths into albums by (artist, album) ONLY.
    Year is still read from tags but not used as part of the key.
    Returns dict mapping (artist, album) -> list of (path, tags) tuples.
    """
    albums: Dict[Tuple[str, str], List[Tuple[Path, Dict]]] = {}
    for f in files:
        tags = get_tags(f)
        if not tags:
            log(f"[WARN] No tags for {f}, skipping.")
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


def format_track_filename(tags: Dict[str, Any], ext: str) -> str:
    """Format a track filename from tags."""
    return f"{tags['tracknum']:02d} - {tags['title']}{ext.lower()}"

