"""
Run state: tracks files modified during the current run.
Used by sync_backups to avoid false "identical" detection when network shares
don't reliably update mtime on write.
"""
from pathlib import Path
from typing import Set

from config import MUSIC_ROOT

# Paths (relative to MUSIC_ROOT) of files we successfully embedded this run.
# sync_backups will never remove backups for these files (we know they differ).
_files_embedded_this_run: Set[str] = set()

# Album summary labels we already reminded about manual tracklist verification (once per run).
_manual_tracklist_warned_labels: Set[str] = set()


def clear() -> None:
    """Clear at start of each run."""
    _files_embedded_this_run.clear()
    _manual_tracklist_warned_labels.clear()


def consume_manual_tracklist_warning_once(album_label: str) -> bool:
    """
    Return True the first time we should emit the compilation / manual tracklist reminder
    for this album label in the current run. Used to dedupe Step 1 vs Step 1.5.
    """
    if album_label in _manual_tracklist_warned_labels:
        return False
    _manual_tracklist_warned_labels.add(album_label)
    return True


def mark_embedded(audio_path: Path) -> None:
    """Record that we successfully embedded art into this file."""
    try:
        rel = audio_path.relative_to(MUSIC_ROOT)
        _files_embedded_this_run.add(str(rel))
    except ValueError:
        pass


def was_embedded(live_file: Path) -> bool:
    """True if we embedded art into this file this run."""
    try:
        rel = live_file.relative_to(MUSIC_ROOT)
        return str(rel) in _files_embedded_this_run
    except ValueError:
        return False
