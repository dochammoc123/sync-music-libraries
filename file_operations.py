"""
File operations: moving, organizing, and cleaning up files.
"""
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import (
    AUDIO_EXT,
    CLEAN_EMPTY_DOWNLOAD_FOLDERS,
    DOWNLOADS_DIR,
    JUNK_FILENAMES,
    MUSIC_ROOT,
)
from logging_utils import (
    add_album_event_label,
    album_label_from_tags,
    log,
)
from tag_operations import choose_album_year, format_track_filename, sanitize_filename_component


def make_album_dir(root: Path, artist: str, album: str, year: str, dry_run: bool = False) -> Path:
    """Create an album directory path and optionally create it."""
    safe_artist = sanitize_filename_component(artist)
    disp_year = f"({year}) " if year else ""
    safe_album = sanitize_filename_component(album)
    album_dir = root / safe_artist / (disp_year + safe_album)
    if not dry_run:
        album_dir.mkdir(parents=True, exist_ok=True)
    return album_dir


def cleanup_download_dirs_for_album(items: List[Tuple[Path, Dict[str, Any]]], dry_run: bool = False) -> None:
    """
    After we've moved an album's audio files out of Downloads/Music
    and processed its artwork, clean up leftover images and junk files,
    then remove any now-empty directories (including empty parent dirs),
    stopping at DOWNLOADS_DIR.
    """
    dirs = {p.parent for (p, _tags) in items}
    dirs = sorted(dirs, key=lambda d: len(str(d)), reverse=True)

    for d in dirs:
        if not d.exists():
            continue

        if dry_run:
            try:
                remaining = [f.name for f in d.iterdir()]
            except FileNotFoundError:
                remaining = []
            log(f"[CLEANUP DRY] Would inspect {d} (remaining: {remaining})")
            continue

        for f in list(d.iterdir()):
            name = f.name
            suffix = f.suffix.lower()

            if suffix in {".jpg", ".jpeg", ".png", ".gif"}:
                log(f"[CLEANUP] Removing leftover image in downloads: {f}")
                try:
                    f.unlink()
                except Exception as e:
                    log(f"[CLEANUP WARN] Could not delete {f}: {e}")
                    continue

            elif name in JUNK_FILENAMES:
                log(f"[CLEANUP] Removing junk file in downloads: {f}")
                try:
                    f.unlink()
                except Exception as e:
                    log(f"[CLEANUP WARN] Could not delete junk {f}: {e}")
                    continue

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
                    if f.is_file() and f.name in JUNK_FILENAMES:
                        try:
                            log(f"[CLEANUP] Removing junk file in downloads: {f}")
                            f.unlink()
                        except Exception as e:
                            log(f"[CLEANUP WARN] Could not delete junk {f}: {e}")
                            remaining.append(f)
                    else:
                        remaining.append(f)

                if remaining:
                    break

            log(f"[CLEANUP] Removing empty download folder: {current}")
            try:
                current.rmdir()
            except Exception as e:
                log(f"[CLEANUP WARN] Could not remove {current}: {e}")
                break

            current = current.parent


def move_album_from_downloads(
    album_key: Tuple[str, str],
    items: List[Tuple[Path, Dict[str, Any]]],
    music_root: Path,
    dry_run: bool = False
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

    for src, tags in items_sorted:
        ext = src.suffix
        filename = format_track_filename(tags, ext)
        if len(discs) > 1:
            disc_label = f"CD{tags['discnum']}"
            disc_dir = album_dir / disc_label
            if not dry_run:
                disc_dir.mkdir(exist_ok=True)
            dest = disc_dir / filename
        else:
            dest = album_dir / filename

        log(f"  MOVE: {src} -> {dest}")
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))

    predownloaded_art = find_predownloaded_art_source_for_album(items)
    used_predownloaded_art = predownloaded_art is not None

    if used_predownloaded_art:
        log(f"  PRE-DOWNLOADED ART: using {predownloaded_art.name} as album artwork source")
        cover_dest = album_dir / "cover.jpg"
        folder_dest = album_dir / "folder.jpg"
        if not dry_run:
            cover_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(predownloaded_art, cover_dest)
            shutil.copy2(predownloaded_art, folder_dest)
        add_album_event_label(label, "Art found pre-downloaded.")
    else:
        log("  No pre-downloaded art files found (large_cover/folder/cover).")

    ensure_cover_and_folder(
        album_dir,
        items_sorted,
        artist,
        album,
        label,
        dry_run=dry_run,
        skip_cover_creation=used_predownloaded_art
    )

    if CLEAN_EMPTY_DOWNLOAD_FOLDERS:
        cleanup_download_dirs_for_album(items, dry_run)


def process_downloads(dry_run: bool = False) -> None:
    """Process all albums in the downloads directory."""
    from tag_operations import find_audio_files, group_by_album
    
    log(f"Scanning downloads: {DOWNLOADS_DIR}")
    audio_files = list(find_audio_files(DOWNLOADS_DIR))
    if not audio_files:
        log("No audio files found in downloads.")
        return

    albums = group_by_album(audio_files, downloads_root=DOWNLOADS_DIR)
    log(f"Found {len(albums)} album(s) in downloads.")

    for idx, (album_key, items) in enumerate(albums.items(), start=1):
        artist, album = album_key
        year = choose_album_year(items)

        if year:
            log(f"[DOWNLOAD] Album {idx}/{len(albums)}: {artist} - {album} ({year})")
        else:
            log(f"[DOWNLOAD] Album {idx}/{len(albums)}: {artist} - {album}")

        move_album_from_downloads(album_key, items, MUSIC_ROOT, dry_run)


def upgrade_albums_to_flac_only(dry_run: bool = False) -> None:
    """Enforce FLAC-only where FLAC exists by removing other audio formats."""
    from logging_utils import add_album_warning_label, album_label_from_dir
    
    log(f"\n[UPGRADE] Enforcing FLAC-only where FLAC exists...")
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        p = Path(dirpath)
        exts = {Path(name).suffix.lower()
                for name in filenames
                if Path(name).suffix.lower() in AUDIO_EXT}
        if ".flac" not in exts:
            continue

        did_cleanup = False

        for name in filenames:
            f = p / name
            ext = f.suffix.lower()
            if ext in AUDIO_EXT and ext != ".flac":
                log(f"  DELETE (non-FLAC): {f}")
                did_cleanup = True
                if not dry_run:
                    try:
                        f.unlink()
                    except OSError as e:
                        log(f"    [WARN] Could not delete {f}: {e}")
                        label = album_label_from_dir(p)
                        add_album_warning_label(label, f"[WARN] Could not delete {f}: {e}")

        if did_cleanup:
            from logging_utils import add_album_event_label
            label = album_label_from_dir(p)
            add_album_event_label(label, "FLAC-only cleanup.")

