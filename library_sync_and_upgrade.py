#!/usr/bin/env python3
"""
library_sync_and_upgrade.py

Modes:
  --mode normal   : Process new downloads, updates overlay, embed missing art, enforce FLAC-only, sync to T8.
  --mode embed    : Same as normal, but ALSO embed cover.jpg from UPDATE overlay into FLACs (with backup).
  --mode restore  : Restore FLACs from backup and sync to T8.

Flags:
  --dry           : Dry-run. Log actions, but make no changes.
"""

import os
import sys
import platform
from pathlib import Path
import shutil
import argparse
import logging
from logging.handlers import RotatingFileHandler
from collections import Counter
from datetime import datetime
from typing import Optional, Dict, List

import subprocess

import requests
import musicbrainzngs
from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC

# ===================== ENVIRONMENT CONFIG =====================

SYSTEM = platform.system()  # "Windows", "Darwin", "Linux", etc.

def icloud_dir() -> Path:
    """
    Return the path to your iCloud root on each OS.
    Adjust these if your layout is different.
    """
    home = Path.home()
    if SYSTEM == "Windows":
        return home / "iCloudDrive"
    elif SYSTEM == "Darwin":
        return home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    else:
        return home / "scripts"

ICLOUD = icloud_dir()
SCRIPTS_ROOT = ICLOUD / "scripts"

# Per-OS config
if SYSTEM == "Windows":
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT    = Path("D:/TestMusicLibrary/ROON/Music")
    T8_ROOT       = Path("D:/TestMusicLibrary/T8/Music")
    LOG_FILE      = SCRIPTS_ROOT / "Logs" / "library_sync_windows.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_windows_summary.log"

elif SYSTEM == "Darwin":
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT    = ICLOUD / "TestMusicLibrary" / "ROON" / "Music"
    T8_ROOT       = ICLOUD / "TestMusicLibrary" / "T8" / "Music"
    LOG_FILE      = SCRIPTS_ROOT / "Logs" / "library_sync_macos.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_macos_summary.log"

else:
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT    = Path.home() / "Music" / "Library"
    T8_ROOT       = None
    LOG_FILE      = SCRIPTS_ROOT / "Logs" /  "library_sync_other.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_other_summary.log"

# ===================== CONFIG =====================

# Backup root for original FLACs before embedded-art changes
BACKUP_ROOT = MUSIC_ROOT.parent / "_EmbeddedArtOriginal"

# Update overlay root – where you drop patch files (cover.jpg, FLACs, etc.)
UPDATE_ROOT = MUSIC_ROOT.parent / "_UpdateOverlay"

# Audio file extensions considered "audio"
AUDIO_EXT = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".wma", ".m4v"}

# Lossless extension we want to KEEP when present
PREFERRED_EXT = ".flac"

# MusicBrainz identity (be polite)
MB_APP = "CaptChrisLibraryScript"
MB_VER = "1.0"
MB_CONTACT = "dochammoc@gmail.com"  # optional but recommended

# Delete empty directories under DOWNLOADS_DIR after moving files?
CLEAN_EMPTY_DOWNLOAD_FOLDERS = True

LOG_MAX_BYTES = 1_000_000    # ~1 MB per log file
LOG_BACKUP_COUNT = 5         # keep up to 5 old logs

WEB_ART_LOOKUP_TIMEOUT = 4       # seconds per fetch attempt
WEB_ART_LOOKUP_RETRIES = 3       # number of attempts
ENABLE_WEB_ART_LOOKUP = True     # enable/disable web art lookups

# Globals that will be set by command-line options
DRY_RUN = False
BACKUP_ORIGINAL_FLAC_BEFORE_EMBED = True
RESTORE_FROM_BACKUP_MODE = False

# Embedding behavior flags
EMBED_IF_MISSING = False        # embed cover.jpg only into FLACs that currently lack embedded art
EMBED_FROM_UPDATES = False      # in embed mode, force embed for albums with cover.jpg from UPDATE_ROOT
EMBED_ALL = False               # advanced: embed cover.jpg into all FLACs in all albums

# Album + global summary structures
# label -> {"events": [...], "warnings": [...]}
ALBUM_SUMMARY: Dict[str, Dict[str, List[str]]] = {}
GLOBAL_WARNINGS: List[str] = []

# Files we consider ignorable "junk" in download folders
JUNK_FILENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

logger = logging.getLogger("library_sync")

# ===================== LOGGING + SUMMARY HELPERS =====================

def setup_logging() -> None:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    if LOG_FILE is not None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)


def log(msg: str) -> None:
    """Main log function: always goes to the rotating log and console."""
    logger.info(msg)


def _get_album_entry(label: str) -> Dict[str, List[str]]:
    entry = ALBUM_SUMMARY.setdefault(label, {"events": [], "warnings": []})
    return entry


def add_album_event_label(label: str, text: str) -> None:
    entry = _get_album_entry(label)
    entry["events"].append(text)


def add_album_warning_label(label: str, text: str) -> None:
    entry = _get_album_entry(label)
    entry["warnings"].append(text)


def add_global_warning(text: str) -> None:
    GLOBAL_WARNINGS.append(text)


def album_label_from_tags(artist: str, album: str, year: str) -> str:
    return f"{artist} - {album} ({year})" if year else f"{artist} - {album}"


def album_label_from_dir(album_dir: Path) -> str:
    """
    Build a label from the directory under MUSIC_ROOT, e.g.
    'Artist - (1995) Album'. Falls back to path if odd.
    """
    try:
        rel = album_dir.relative_to(MUSIC_ROOT)
    except ValueError:
        return album_dir.as_posix()

    # Collapse CD1/CD2 etc to album folder
    parts = list(rel.parts)
    if parts and parts[-1].upper().startswith("CD") and len(parts) >= 2:
        parts = parts[:-1]

    if len(parts) >= 2:
        artist = parts[0]
        album_folder = parts[1]
        return f"{artist} - {album_folder}"
    else:
        return rel.as_posix()


def write_summary_log(mode: str) -> None:
    """
    Write a compact summary log containing:
      - Run timestamp, mode, DRY_RUN
      - Albums processed (events + warnings grouped)
      - Global warnings
    Overwrites on each run.
    """
    try:
        SUMMARY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    lines: List[str] = []
    lines.append(f"Library sync summary - {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"Mode: {mode}, DRY_RUN={DRY_RUN}")
    lines.append("")

    if ALBUM_SUMMARY:
        lines.append("Albums processed:")
        for label in sorted(ALBUM_SUMMARY.keys()):
            entry = ALBUM_SUMMARY[label]
            lines.append(f"  {label}")
            for e in entry["events"]:
                lines.append(f"\t- {e}")
            for w in entry["warnings"]:
                lines.append(f"\t{w}")
    else:
        lines.append("Albums processed: (none)")

    lines.append("")
    lines.append("Global warnings:")
    if GLOBAL_WARNINGS:
        for w in GLOBAL_WARNINGS:
            lines.append(f"  {w}")
    else:
        lines.append("  (none)")

    try:
        with SUMMARY_LOG_FILE.open("w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        logger.info(f"[WARN] Could not write summary log: {e}")


def notify_run_summary(mode: str) -> None:
    """
    Simple cross-platform notification at the end of a run,
    mentioning whether there were warnings.
    """
    total_warnings = sum(len(v["warnings"]) for v in ALBUM_SUMMARY.values()) + len(GLOBAL_WARNINGS)

    if total_warnings == 0:
        title = "Library Sync Complete"
        message = f"Mode: {mode} — finished with no warnings."
    else:
        title = "Library Sync Complete (Warnings)"
        message = f"Mode: {mode} — finished with {total_warnings} warning(s)."

    # macOS Notification
    if SYSTEM == "Darwin":
        try:
            subprocess.run([
                "osascript", "-e",
                f'display notification "{message}" with title "{title}"'
            ], check=False)
        except Exception as e:
            log(f"[WARN] macOS notification failed: {e}")

    # Windows Notification
    elif SYSTEM == "Windows":
        try:
            import ctypes
            MB_OK = 0x0
            ctypes.windll.user32.MessageBoxW(0, message, title, MB_OK)
        except Exception as e:
            log(f"[WARN] Windows notification failed: {e}")

    # Other OS: no-op beyond log line


def show_summary_log_in_viewer():
    """
    Open the summary log in a simple viewer:
      - macOS: TextEdit via 'open'
      - Windows: default associated app via os.startfile (usually Notepad)
    Safe no-op if the file doesn't exist.
    """
    try:
        if not SUMMARY_LOG_FILE.exists():
            log(f"[WARN] Summary log {SUMMARY_LOG_FILE} does not exist; nothing to show.")
            return

        if SYSTEM == "Darwin":
            # Open with default app (TextEdit by default)
            subprocess.run(
                ["open", str(SUMMARY_LOG_FILE)],
                check=False
            )
        elif SYSTEM == "Windows":
            os.startfile(str(SUMMARY_LOG_FILE))
        else:
            # On other OS's, just print a hint
            log(f"[INFO] Summary log is at: {SUMMARY_LOG_FILE}")
    except Exception as e:
        log(f"[WARN] Could not open summary log viewer: {e}")


def print_summary_log_to_stdout():
    """
    Print the summary log contents to stdout at the end of the run.
    Safe no-op if file doesn't exist.
    """
    try:
        if not SUMMARY_LOG_FILE.exists():
            log(f"[WARN] Summary log {SUMMARY_LOG_FILE} does not exist; nothing to print.")
            return

        print("\n================ SUMMARY ================")
        with SUMMARY_LOG_FILE.open("r", encoding="utf-8") as f:
            print(f.read())
        print("=========================================\n")
    except Exception as e:
        log(f"[WARN] Could not print summary log: {e}")


# ===================== CORE HELPERS =====================

def init_musicbrainz() -> None:
    musicbrainzngs.set_useragent(MB_APP, MB_VER, MB_CONTACT)


def find_audio_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in AUDIO_EXT:
                yield p


def get_tags(path: Path):
    """Return tags dict from a file: artist, album, year, tracknum, discnum, title."""
    audio = MutagenFile(str(path), easy=True)
    if audio is None or not audio.tags:
        return None

    def _get(tag, default=""):
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


def group_by_album(files):
    """
    Group paths into albums by (artist, album) ONLY.
    Year is still read from tags but not used as part of the key.
    """
    albums = {}
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


def choose_album_year(items) -> str:
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

    from collections import Counter
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


def make_album_dir(root: Path, artist: str, album: str, year: str) -> Path:
    safe_artist = sanitize_filename_component(artist)
    disp_year = f"({year}) " if year else ""
    safe_album = sanitize_filename_component(album)
    album_dir = root / safe_artist / (disp_year + safe_album)
    if not DRY_RUN:
        album_dir.mkdir(parents=True, exist_ok=True)
    return album_dir


def format_track_filename(tags, ext: str) -> str:
    safe_title = sanitize_filename_component(tags["title"])
    return f"{tags['tracknum']:02d} - {safe_title}{ext.lower()}"


def export_embedded_art_to_cover(first_file: Path, cover_path: Path) -> bool:
    mf = MutagenFile(str(first_file))
    if mf is None:
        return False

    if isinstance(mf, FLAC):
        if mf.pictures:
            if not DRY_RUN:
                cover_path.write_bytes(mf.pictures[0].data)
            return True
        return False

    try:
        id3 = ID3(str(first_file))
        pics = [f for f in id3.values() if isinstance(f, APIC)]
        if pics:
            if not DRY_RUN:
                cover_path.write_bytes(pics[0].data)
            return True
    except Exception:
        pass

    return False


def fetch_art_from_web(artist: str, album: str, cover_path: Path) -> bool:
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
                    if not DRY_RUN:
                        cover_path.write_bytes(r.content)
                    return True
            except Exception as e:
                log(f"    [WEB WARN] Attempt {attempt} failed: {e}")

        return False

    except Exception as e:
        log(f"  [WARN] Art lookup failed for {artist} - {album}: {e}")
        return False


def find_predownloaded_art_source_for_album(items):
    """
    Given the list of (path, tags) for an album's tracks in DOWNLOADS_DIR,
    look in their parent directories for standard art files:
    large_cover.jpg, folder.jpg, cover.jpg (in that priority).
    Returns a Path or None.
    """
    candidate_dirs = {p.parent for (p, _tags) in items}
    art_priority = ["large_cover.jpg", "folder.jpg", "cover.jpg"]

    for art_name in art_priority:
        for d in candidate_dirs:
            candidate = d / art_name
            if candidate.exists():
                return candidate
    return None


def ensure_cover_and_folder(album_dir: Path,
                            album_files,
                            artist: str,
                            album: str,
                            label: Optional[str],
                            skip_cover_creation: bool = False) -> None:
    """
    Ensure cover.jpg and folder.jpg exist, using (in order):
      - Standard pre-downloaded art (if already copied),
      - embedded art from first track,
      - web lookup via MusicBrainz.
    """
    cover_path = album_dir / "cover.jpg"
    folder_path = album_dir / "folder.jpg"

    if not skip_cover_creation:
        if not cover_path.exists():
            log("  No cover.jpg; attempting to export embedded art…")
            first_file = album_files[0][0]
            if export_embedded_art_to_cover(first_file, cover_path):
                log("  cover.jpg created from embedded art.")
                if label:
                    add_album_event_label(label, "Found art (embedded).")
            else:
                log("  No embedded art; attempting web fetch…")
                if fetch_art_from_web(artist, album, cover_path):
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
            log("  Creating folder.jpg from cover.jpg")
            if not DRY_RUN:
                shutil.copy2(cover_path, folder_path)


def backup_flac_if_needed(flac_path: Path) -> None:
    """
    If BACKUP_ORIGINAL_FLAC_BEFORE_EMBED is True, create a backup copy
    of this FLAC under BACKUP_ROOT, mirroring MUSIC_ROOT structure.
    Only create if it does not already exist.
    """
    if not BACKUP_ORIGINAL_FLAC_BEFORE_EMBED:
        return
    try:
        rel = flac_path.relative_to(MUSIC_ROOT)
    except ValueError:
        return
    backup_path = BACKUP_ROOT / rel
    if backup_path.exists():
        return
    log(f"  BACKUP: {flac_path} -> {backup_path}")
    if not DRY_RUN:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(flac_path, backup_path)


def embed_art_into_flacs(album_dir: Path) -> None:
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
                backup_flac_if_needed(p)
                log(f"  EMBED: updating embedded art in {p}")
                if not DRY_RUN:
                    audio = FLAC(str(p))
                    audio.clear_pictures()
                    pic = Picture()
                    pic.data = img_data
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    pic.desc = "Cover"
                    audio.add_picture(pic)
                    audio.save()


def embed_missing_art_global() -> None:
    """
    Walk the entire MUSIC_ROOT and embed cover.jpg into FLACs
    that currently have no embedded artwork.
    Skips unreadable / non-FLAC files gracefully.
    """
    if not EMBED_IF_MISSING:
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

            if DRY_RUN:
                continue

            backup_flac_if_needed(p)

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


def fixup_missing_art() -> None:
    """
    Final pass: scan library for album dirs with audio files but no cover.jpg
    and try to create art (embedded -> web).
    """
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

        if export_embedded_art_to_cover(first_audio_path, cover_path):
            log("    Extracted embedded art.")
            add_album_event_label(label, "Found missing art (embedded).")
            continue

        if fetch_art_from_web(artist, album, cover_path):
            log("    Downloaded cover via web.")
            add_album_event_label(label, "Found missing art (web).")
            continue

        msg = "[WARN] Could not obtain artwork."
        log(f"    {msg}")
        add_album_warning_label(label, msg)


# ===================== DOWNLOADS PROCESSING =====================

def cleanup_download_dirs_for_album(items) -> None:
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

        if DRY_RUN:
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


def move_album_from_downloads(album_key, items, music_root: Path) -> None:
    artist, album = album_key
    year = choose_album_year(items)
    label = album_label_from_tags(artist, album, year)

    safe_artist = artist.replace(":", " -")
    disp_year = f"({year}) " if year else ""
    safe_album = album.replace(":", " -")
    album_dir = music_root / safe_artist / (disp_year + safe_album)
    existing = album_dir.exists()

    album_dir = make_album_dir(music_root, artist, album, year)

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
            if not DRY_RUN:
                disc_dir.mkdir(exist_ok=True)
            dest = disc_dir / filename
        else:
            dest = album_dir / filename

        log(f"  MOVE: {src} -> {dest}")
        if not DRY_RUN:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))

    predownloaded_art = find_predownloaded_art_source_for_album(items)
    used_predownloaded_art = predownloaded_art is not None

    if used_predownloaded_art:
        log(f"  PRE-DOWNLOADED ART: using {predownloaded_art.name} as album artwork source")
        cover_dest = album_dir / "cover.jpg"
        folder_dest = album_dir / "folder.jpg"
        if not DRY_RUN:
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
        skip_cover_creation=used_predownloaded_art
    )

    if CLEAN_EMPTY_DOWNLOAD_FOLDERS:
        cleanup_download_dirs_for_album(items)


def process_downloads() -> None:
    log(f"Scanning downloads: {DOWNLOADS_DIR}")
    audio_files = list(find_audio_files(DOWNLOADS_DIR))
    if not audio_files:
        log("No audio files found in downloads.")
        return

    albums = group_by_album(audio_files)
    log(f"Found {len(albums)} album(s) in downloads.")

    for idx, (album_key, items) in enumerate(albums.items(), start=1):
        artist, album = album_key
        year = choose_album_year(items)

        if year:
            log(f"[DOWNLOAD] Album {idx}/{len(albums)}: {artist} - {album} ({year})")
        else:
            log(f"[DOWNLOAD] Album {idx}/{len(albums)}: {artist} - {album}")

        move_album_from_downloads(album_key, items, MUSIC_ROOT)


# ===================== UPDATE OVERLAY =====================

def remove_backup_for(rel_path: Path) -> None:
    """
    If a backup exists for this relative path, remove it.
    Used when a NEW original FLAC is copied from UPDATE_ROOT.
    """
    backup_path = BACKUP_ROOT / rel_path
    if backup_path.exists():
        log(f"[BACKUP] Removing obsolete backup: {backup_path}")
        if not DRY_RUN:
            try:
                backup_path.unlink()
            except Exception as e:
                log(f"[BACKUP WARN] Could not delete backup {backup_path}: {e}")


def apply_updates_from_overlay():
    """
    Copy any files found under UPDATE_ROOT into MUSIC_ROOT, mirroring structure.
    - Audio files: treated as new originals; any existing backup for that path is removed.
    - Other files (e.g., cover.jpg) overwrite/create assets in MUSIC_ROOT.

    Files in UPDATE_ROOT are deleted after being applied.
    Returns:
      updated_album_dirs: set of album directories in MUSIC_ROOT that were touched.
      albums_with_new_cover: subset where cover.jpg came from UPDATE_ROOT.
    """
    updated_album_dirs = set()
    albums_with_new_cover = set()

    if not UPDATE_ROOT.exists():
        return updated_album_dirs, albums_with_new_cover

    log(f"\n[UPDATE] Applying overlay from {UPDATE_ROOT} -> {MUSIC_ROOT}")

    for src in UPDATE_ROOT.rglob("*"):
        if src.is_dir():
            continue

        rel = src.relative_to(UPDATE_ROOT)
        dest = MUSIC_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if src.suffix.lower() in AUDIO_EXT:
            log(f"  [UPDATE AUDIO] {src} -> {dest}")
            if not DRY_RUN:
                shutil.copy2(src, dest)
            remove_backup_for(rel)
            updated_album_dirs.add(dest.parent)
        else:
            log(f"  [UPDATE ASSET] {src} -> {dest}")
            if not DRY_RUN:
                shutil.copy2(src, dest)
            updated_album_dirs.add(dest.parent)
            if src.name.lower() == "cover.jpg":
                albums_with_new_cover.add(dest.parent)

        if not DRY_RUN:
            try:
                src.unlink()
            except Exception as e:
                log(f"  [UPDATE WARN] Could not delete applied update file {src}: {e}")

    for album_dir in updated_album_dirs:
        label = album_label_from_dir(album_dir)
        add_album_event_label(label, "Updated from overlay.")

    return updated_album_dirs, albums_with_new_cover


def sync_update_root_structure() -> None:
    """
    Ensure UPDATE_ROOT has the same directory structure as MUSIC_ROOT, but no files.
    Remove any directories in UPDATE_ROOT that don't exist in MUSIC_ROOT.
    """
    if UPDATE_ROOT is None:
        return

    log(f"\n[UPDATE] Syncing empty overlay directory structure under {UPDATE_ROOT}")

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        rel = Path(dirpath).relative_to(MUSIC_ROOT)
        upd_dir = UPDATE_ROOT / rel
        if not DRY_RUN:
            upd_dir.mkdir(parents=True, exist_ok=True)

    for dirpath, dirnames, filenames in os.walk(UPDATE_ROOT, topdown=False):
        upd_dir = Path(dirpath)
        rel = upd_dir.relative_to(UPDATE_ROOT)
        music_dir = MUSIC_ROOT / rel

        if not music_dir.exists():
            log(f"  [UPDATE] Removing obsolete overlay dir: {upd_dir}")
            if not DRY_RUN:
                try:
                    upd_dir.rmdir()
                except OSError:
                    pass
        else:
            if not DRY_RUN:
                for f in list(upd_dir.iterdir()):
                    if f.is_file():
                        log(f"  [UPDATE] Removing stray file from overlay: {f}")
                        try:
                            f.unlink()
                        except OSError:
                            pass


# ===================== UPGRADE + SYNC + RESTORE =====================

def upgrade_albums_to_flac_only() -> None:
    log(f"\n[UPGRADE] Enforcing FLAC-only where FLAC exists...")
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        p = Path(dirpath)
        exts = {Path(name).suffix.lower()
                for name in filenames
                if Path(name).suffix.lower() in AUDIO_EXT}
        if PREFERRED_EXT not in exts:
            continue

        did_cleanup = False

        for name in filenames:
            f = p / name
            ext = f.suffix.lower()
            if ext in AUDIO_EXT and ext != PREFERRED_EXT:
                log(f"  DELETE (non-FLAC): {f}")
                did_cleanup = True
                if not DRY_RUN:
                    try:
                        f.unlink()
                    except OSError as e:
                        log(f"    [WARN] Could not delete {f}: {e}")
                        label = album_label_from_dir(p)
                        add_album_warning_label(label, f"[WARN] Could not delete {f}: {e}")

        if did_cleanup:
            label = album_label_from_dir(p)
            add_album_event_label(label, "FLAC-only cleanup.")


def sync_music_to_t8() -> None:
    if T8_ROOT is None:
        log("\n[T8 SYNC] T8_ROOT is None, skipping sync.")
        return

    log(f"\n[T8 SYNC] Mirroring {MUSIC_ROOT} -> {T8_ROOT}")

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        src_dir = Path(dirpath)
        rel = src_dir.relative_to(MUSIC_ROOT)
        dst_dir = T8_ROOT / rel
        if not DRY_RUN:
            dst_dir.mkdir(parents=True, exist_ok=True)

        for name in filenames:
            src_file = src_dir / name
            ext = src_file.suffix.lower()
            if ext == ".flac" or name.lower() in ("cover.jpg", "folder.jpg") or ext in {".jpg", ".jpeg", ".png"}:
                dst_file = dst_dir / name
                if (not dst_file.exists()
                        or src_file.stat().st_mtime > dst_file.stat().st_mtime):
                    log(f"  COPY: {src_file} -> {dst_file}")
                    if not DRY_RUN:
                        shutil.copy2(src_file, dst_file)

    for dirpath, dirnames, filenames in os.walk(T8_ROOT, topdown=False):
        dst_dir = Path(dirpath)
        rel = dst_dir.relative_to(T8_ROOT)
        src_dir = MUSIC_ROOT / rel

        for name in filenames:
            dst_file = dst_dir / name
            ext = dst_file.suffix.lower()
            if ext == ".flac" or name.lower() in ("cover.jpg", "folder.jpg") or ext in {".jpg", ".jpeg", ".png"}:
                src_file = src_dir / name
                if not src_file.exists():
                    log(f"  DELETE on T8 (no source): {dst_file}")
                    if not DRY_RUN:
                        try:
                            dst_file.unlink()
                        except OSError as e:
                            log(f"    [WARN] Could not delete {dst_file}: {e}")

        if not os.listdir(dst_dir):
            log(f"  REMOVE empty dir on T8: {dst_dir}")
            if not DRY_RUN:
                try:
                    dst_dir.rmdir()
                except OSError:
                    pass


def restore_flacs_from_backups() -> None:
    """
    Restore FLACs from BACKUP_ROOT into MUSIC_ROOT and delete backups.
    Only affects files that have backup copies.
    """
    log(f"\n[RESTORE] Restoring FLACs from backup under {BACKUP_ROOT}")
    if not BACKUP_ROOT.exists():
        log("  No backup root found; nothing to restore.")
        return

    for dirpath, dirnames, filenames in os.walk(BACKUP_ROOT, topdown=False):
        backup_dir = Path(dirpath)
        for name in filenames:
            backup_file = backup_dir / name
            if backup_file.suffix.lower() != ".flac":
                continue
            rel = backup_file.relative_to(BACKUP_ROOT)
            dest = MUSIC_ROOT / rel
            log(f"  RESTORE: {backup_file} -> {dest}")
            if not DRY_RUN:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, dest)
                try:
                    backup_file.unlink()
                except OSError as e:
                    log(f"    [WARN] Could not delete backup {backup_file}: {e}")
                    label = album_label_from_dir(dest.parent)
                    add_album_warning_label(label, f"[WARN] Could not delete backup {backup_file}: {e}")

        if not DRY_RUN:
            try:
                if not os.listdir(backup_dir):
                    backup_dir.rmdir()
            except OSError:
                pass


# ===================== MAIN =====================

def main() -> None:
    parser = argparse.ArgumentParser(description="Music Library Automation Script")
    parser.add_argument(
        "--mode",
        choices=["normal", "embed", "restore"],
        default="normal",
        help="Run mode: normal, embed (update embedded art using Update overlay), or restore (restore FLACs from backup)"
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="Dry run mode (no file modifications)"
    )
    parser.add_argument(
        "--embed-all",
        action="store_true",
        help=argparse.SUPPRESS
    )

    args = parser.parse_args()

    global DRY_RUN, BACKUP_ORIGINAL_FLAC_BEFORE_EMBED, RESTORE_FROM_BACKUP_MODE
    global EMBED_IF_MISSING, EMBED_FROM_UPDATES, EMBED_ALL

    DRY_RUN = args.dry
    EMBED_ALL = args.embed_all

    if args.mode == "normal":
        RESTORE_FROM_BACKUP_MODE = False
        BACKUP_ORIGINAL_FLAC_BEFORE_EMBED = True
        EMBED_IF_MISSING = True
        EMBED_FROM_UPDATES = False
    elif args.mode == "embed":
        RESTORE_FROM_BACKUP_MODE = False
        BACKUP_ORIGINAL_FLAC_BEFORE_EMBED = True
        EMBED_IF_MISSING = True
        EMBED_FROM_UPDATES = True
    elif args.mode == "restore":
        RESTORE_FROM_BACKUP_MODE = True
        BACKUP_ORIGINAL_FLAC_BEFORE_EMBED = False
        EMBED_IF_MISSING = False
        EMBED_FROM_UPDATES = False

    setup_logging()
    log(f"Starting script in mode: {args.mode}")
    log(f"DRY_RUN = {DRY_RUN}")
    log(f"EMBED_ALL = {EMBED_ALL}")

    init_musicbrainz()

    try:
        if RESTORE_FROM_BACKUP_MODE:
            restore_flacs_from_backups()
            sync_music_to_t8()
            log("Restore mode complete.")
            write_summary_log(args.mode)
            notify_run_summary(args.mode)
            return

        log("\nStep 1: Process new downloads (organize + art)...")
        process_downloads()

        log("\nStep 2: Apply UPDATE overlay (files from Update -> Music)...")
        updated_album_dirs, albums_with_new_cover = apply_updates_from_overlay()

        log("\nStep 3: Upgrade albums to FLAC-only where FLAC exists...")
        upgrade_albums_to_flac_only()

        log("\nStep 4: Embed missing artwork (only FLACs with no embedded art)...")
        embed_missing_art_global()

        if EMBED_ALL:
            log("\n[EMBED ALL] Embedding cover.jpg into all FLACs in all albums (advanced mode).")
            for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
                embed_art_into_flacs(Path(dirpath))

        if EMBED_FROM_UPDATES and albums_with_new_cover:
            log("\n[EMBED FROM UPDATES] Embedding new cover.jpg from UPDATE overlay into updated albums...")
            for album_dir in sorted(albums_with_new_cover):
                log(f"  [EMBED FROM UPDATE] Album: {album_dir}")
                embed_art_into_flacs(album_dir)
                label = album_label_from_dir(album_dir)
                add_album_event_label(label, "Embedded new art from overlay.")

        log("\nStep 5: Sync master library to T8...")
        sync_music_to_t8()

        log("\nStep 6: Sync empty UPDATE overlay directory structure...")
        sync_update_root_structure()

        log("\nStep 7: Final missing-art fixup...")
        fixup_missing_art()

        log("\nStep 8: Writing summary log...")
        write_summary_log(args.mode)

        log("Step 9: Run summary notification...")
        notify_run_summary(args.mode)
               
        log("\nRun complete.")

        # Option A: always print summary to console
        print_summary_log_to_stdout()

        # Option B: only open the log in a viewer if there were warnings
        total_warnings = sum(len(v["warnings"]) for v in ALBUM_SUMMARY.values()) + len(GLOBAL_WARNINGS)
        if total_warnings > 0:
            show_summary_log_in_viewer()
            sys.exit(2) # 0 = clean, 1 = errors, 2 = warnings

    except Exception as e:
        logger.exception("Fatal error during run")
        add_global_warning(f"Fatal error during run: {e}")
        write_summary_log(args.mode)
        notify_run_summary(args.mode)
        sys.exit(1)
        # Optional: re-raise in debugging scenarios
        # raise


if __name__ == "__main__":
    main()
