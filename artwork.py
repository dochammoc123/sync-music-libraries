"""
Artwork handling: embedding, fetching, and managing album artwork.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import musicbrainzngs
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC
from mutagen import File as MutagenFile

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


def find_predownloaded_art_source_for_album(items: List[Tuple[Path, Dict[str, Any]]]) -> Optional[Path]:
    """
    Given the list of (path, tags) for an album's tracks in DOWNLOADS_DIR,
    look in their parent directories for standard art files.
    Priority: large_cover.jpg > cover.jpg (for cover.jpg)
    folder.jpg and cover.jpg have equal priority, but if both exist,
    cover.jpg is used for cover.jpg and folder.jpg is preserved separately.
    Returns a Path or None.
    """
    candidate_dirs = {p.parent for (p, _tags) in items}
    # Priority: large_cover.jpg > cover.jpg (folder.jpg handled separately)
    art_priority = ["large_cover.jpg", "cover.jpg"]

    for art_name in art_priority:
        for d in candidate_dirs:
            candidate = d / art_name
            if candidate.exists():
                return candidate
    return None


def backup_flac_if_needed(flac_path: Path, dry_run: bool = False, backup_enabled: bool = True) -> None:
    """
    If backup_enabled is True, create a backup copy of this FLAC under BACKUP_ROOT,
    mirroring MUSIC_ROOT structure. Only create if it does not already exist.
    """
    if not backup_enabled:
        return
    try:
        rel = flac_path.relative_to(MUSIC_ROOT)
    except ValueError:
        return
    backup_path = BACKUP_ROOT / rel
    if backup_path.exists():
        return
    log(f"  BACKUP: {flac_path} -> {backup_path}")
    if not dry_run:
        import shutil
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(flac_path, backup_path)


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
            if tags and tags.get("title") and tags.get("artist") and tags.get("tracknum", 0) > 0:
                files_with_tags.append((audio_file, tags))
                if not album_metadata:
                    album_metadata = {
                        "artist": tags.get("artist"),
                        "album": tags.get("album"),
                        "year": tags.get("year", ""),
                    }
            else:
                files_without_tags.append(audio_file)
        
        # If we have album metadata and files without tags, add tags to them
        if album_metadata and files_without_tags:
            for audio_file in files_without_tags:
                # Get existing tags (may have partial info from filename parsing)
                existing_tags = get_tags(audio_file)
                if not existing_tags:
                    existing_tags = {}
                
                # Build complete tags using album metadata
                tags_to_write = {
                    "artist": album_metadata["artist"],
                    "album": album_metadata["album"],
                    "year": album_metadata.get("year", ""),
                    "tracknum": existing_tags.get("tracknum", 0),
                    "discnum": existing_tags.get("discnum", 1),
                    "title": existing_tags.get("title", audio_file.stem),
                }
                
                # Extract track number from filename if not in tags
                if tags_to_write["tracknum"] == 0:
                    import re
                    match = re.match(r'^(\d+)', audio_file.stem)
                    if match:
                        try:
                            tags_to_write["tracknum"] = int(match.group(1))
                        except ValueError:
                            pass
                
                # Extract title from filename if not in tags
                if not tags_to_write.get("title") or tags_to_write["title"] == audio_file.stem:
                    import re
                    title = audio_file.stem
                    title = re.sub(r'^\d+\s*-\s*', '', title).strip()
                    if title:
                        tags_to_write["title"] = title
                
                log(f"  Adding tags to {audio_file.name}")
                if write_tags_to_file(audio_file, tags_to_write, dry_run, backup_enabled):
                    log(f"    ✓ Added tags to {audio_file.name}")
                else:
                    log(f"    [WARN] Could not add tags to {audio_file.name}")


def embed_missing_art_global(dry_run: bool = False, backup_enabled: bool = True, embed_if_missing: bool = True) -> None:
    """
    Walk the entire MUSIC_ROOT and embed cover.jpg into FLACs
    that currently have no embedded artwork.
    Skips unreadable / non-FLAC files gracefully.
    """
    if not embed_if_missing:
        return
    
    log("\n[EMBED] Embedding cover.jpg into FLACs that have no embedded art...")

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
            if p.suffix.lower() != ".flac":
                continue

            try:
                audio = FLAC(str(p))
            except Exception as e:
                log(f"  [EMBED WARN] Skipping unreadable FLAC {p}: {e}")
                if label:
                    add_album_warning_label(label, f"[WARN] Unreadable FLAC during embed: {p}")
                continue

            if getattr(audio, "pictures", None):
                continue

            if cover_data is None:
                try:
                    cover_data = cover_path.read_bytes()
                except Exception as e:
                    log(f"  [EMBED WARN] Could not read cover.jpg in {album_dir}: {e}")
                    if label:
                        add_album_warning_label(label, f"[WARN] Could not read cover.jpg: {e}")
                    break

            log(f"  EMBED (missing art): {p}")
            embedded_any = True

            if dry_run:
                continue

            backup_flac_if_needed(p, dry_run, backup_enabled)

            pic = Picture()
            pic.data = cover_data
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Cover"

            try:
                audio.add_picture(pic)
                audio.save()
            except Exception as e:
                log(f"  [EMBED WARN] Failed to embed art into {p}: {e}")
                if label:
                    add_album_warning_label(label, f"[WARN] Failed to embed art into {p}: {e}")

        if embedded_any and label:
            add_album_event_label(label, "Embedded missing art.")


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

