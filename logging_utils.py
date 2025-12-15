"""
Logging utilities for music library sync script.
Handles logging setup, summary generation, and notifications.
"""
import logging
import os
import platform
import subprocess
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional

from config import LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT, SUMMARY_LOG_FILE, SYSTEM

logger = logging.getLogger("library_sync")

# Album + global summary structures
# label -> {"events": [...], "warnings": [...]}
ALBUM_SUMMARY: Dict[str, Dict[str, List[str]]] = {}
GLOBAL_WARNINGS: List[str] = []


def setup_logging() -> None:
    """Configure logging with both console and file handlers."""
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
    """Get or create an album entry in the summary."""
    entry = ALBUM_SUMMARY.setdefault(label, {"events": [], "warnings": []})
    return entry


def add_album_event_label(label: str, text: str) -> None:
    """Add an event message to an album's summary."""
    entry = _get_album_entry(label)
    entry["events"].append(text)


def add_album_warning_label(label: str, text: str) -> None:
    """Add a warning message to an album's summary."""
    entry = _get_album_entry(label)
    entry["warnings"].append(text)


def add_global_warning(text: str) -> None:
    """Add a global warning message."""
    GLOBAL_WARNINGS.append(text)


def album_label_from_tags(artist: str, album: str, year: str) -> str:
    """Create an album label from tags."""
    return f"{artist} - {album} ({year})" if year else f"{artist} - {album}"


def album_label_from_dir(album_dir: Path) -> str:
    """
    Build a label from the directory under MUSIC_ROOT, e.g.
    'Artist - Album (1995)'. Normalizes year format to match album_label_from_tags().
    Falls back to path if odd.
    """
    from config import MUSIC_ROOT
    import re
    
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
        
        # Extract year from album folder if it's at the beginning: "(2012) Album Name"
        # Normalize to match album_label_from_tags() format: "Artist - Album (2012)"
        year_match = re.match(r'^\((\d{4})\)\s*(.+)$', album_folder)
        if year_match:
            year = year_match.group(1)
            album = year_match.group(2).strip()
            return f"{artist} - {album} ({year})"
        else:
            # No year prefix, use as-is
            return f"{artist} - {album_folder}"
    else:
        return rel.as_posix()


def write_summary_log(mode: str, dry_run: bool = False) -> None:
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
    lines.append(f"Mode: {mode}, DRY_RUN={dry_run}")
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
    Now just logs to console - no blocking prompts.
    """
    total_warnings = sum(len(v["warnings"]) for v in ALBUM_SUMMARY.values()) + len(GLOBAL_WARNINGS)

    if total_warnings == 0:
        message = f"Mode: {mode} — finished with no warnings."
    else:
        message = f"Mode: {mode} — finished with {total_warnings} warning(s)."

    log(f"Run complete: {message}")

    # macOS Notification (non-blocking)
    if SYSTEM == "Darwin":
        try:
            subprocess.run([
                "osascript", "-e",
                f'display notification "{message}" with title "Library Sync Complete"'
            ], check=False)
        except Exception as e:
            log(f"[WARN] macOS notification failed: {e}")

    # Windows: No blocking MessageBox - just log to console
    # The console and summary log viewer will remain open for user to review

    # Other OS: no-op beyond log line


def show_summary_log_in_viewer() -> None:
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


def print_summary_log_to_stdout() -> None:
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

