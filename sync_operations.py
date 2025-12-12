"""
Sync operations: T8 sync, update overlay, and restore operations.
"""
import os
import shutil
from pathlib import Path
from typing import Set, Tuple

from config import AUDIO_EXT, BACKUP_ROOT, CLEAN_EMPTY_BACKUP_FOLDERS, MUSIC_ROOT, T8_ROOT, UPDATE_ROOT
from logging_utils import (
    add_album_event_label,
    album_label_from_dir,
    add_album_warning_label,
    log,
)


def remove_backup_for(rel_path: Path, dry_run: bool = False) -> None:
    """
    If a backup exists for this relative path, remove it.
    Used when a NEW original FLAC is copied from UPDATE_ROOT.
    """
    backup_path = BACKUP_ROOT / rel_path
    if backup_path.exists():
        log(f"[BACKUP] Removing obsolete backup: {backup_path}")
        if not dry_run:
            try:
                backup_path.unlink()
            except Exception as e:
                log(f"[BACKUP WARN] Could not delete backup {backup_path}: {e}")


def apply_updates_from_overlay(dry_run: bool = False) -> Tuple[Set[Path], Set[Path]]:
    """
    Copy any files found under UPDATE_ROOT into MUSIC_ROOT, mirroring structure.
    - Audio files: treated as new originals; any existing backup for that path is removed.
    - Other files (e.g., cover.jpg) overwrite/create assets in MUSIC_ROOT.

    Files in UPDATE_ROOT are deleted after being applied.
    Returns:
      updated_album_dirs: set of album directories in MUSIC_ROOT that were touched.
      albums_with_new_cover: subset where cover.jpg came from UPDATE_ROOT.
    """
    updated_album_dirs: Set[Path] = set()
    albums_with_new_cover: Set[Path] = set()

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
            if not dry_run:
                shutil.copy2(src, dest)
            remove_backup_for(rel, dry_run)
            updated_album_dirs.add(dest.parent)
        else:
            log(f"  [UPDATE ASSET] {src} -> {dest}")
            if not dry_run:
                shutil.copy2(src, dest)
            updated_album_dirs.add(dest.parent)
            if src.name.lower() == "cover.jpg":
                albums_with_new_cover.add(dest.parent)

        if not dry_run:
            try:
                src.unlink()
            except Exception as e:
                log(f"  [UPDATE WARN] Could not delete applied update file {src}: {e}")

    for album_dir in updated_album_dirs:
        label = album_label_from_dir(album_dir)
        add_album_event_label(label, "Updated from overlay.")

    return updated_album_dirs, albums_with_new_cover


def sync_update_root_structure(dry_run: bool = False) -> None:
    """
    Ensure UPDATE_ROOT has the same directory structure as MUSIC_ROOT, but no files.
    Remove any directories in UPDATE_ROOT that don't exist in MUSIC_ROOT.
    """
    if not UPDATE_ROOT or not UPDATE_ROOT.exists():
        return

    log(f"\n[UPDATE] Syncing empty overlay directory structure under {UPDATE_ROOT}")

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        rel = Path(dirpath).relative_to(MUSIC_ROOT)
        upd_dir = UPDATE_ROOT / rel
        if not dry_run:
            upd_dir.mkdir(parents=True, exist_ok=True)

    for dirpath, dirnames, filenames in os.walk(UPDATE_ROOT, topdown=False):
        upd_dir = Path(dirpath)
        rel = upd_dir.relative_to(UPDATE_ROOT)
        music_dir = MUSIC_ROOT / rel

        if not music_dir.exists():
            log(f"  [UPDATE] Removing obsolete overlay dir: {upd_dir}")
            if not dry_run:
                try:
                    upd_dir.rmdir()
                except OSError:
                    pass
        else:
            if not dry_run:
                for f in list(upd_dir.iterdir()):
                    if f.is_file():
                        log(f"  [UPDATE] Removing stray file from overlay: {f}")
                        try:
                            f.unlink()
                        except OSError:
                            pass


def sync_music_to_t8(dry_run: bool = False) -> None:
    """Sync master library to T8 destination."""
    if T8_ROOT is None:
        log("\n[T8 SYNC] T8_ROOT is None, skipping sync.")
        return

    log(f"\n[T8 SYNC] Mirroring {MUSIC_ROOT} -> {T8_ROOT}")

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        src_dir = Path(dirpath)
        rel = src_dir.relative_to(MUSIC_ROOT)
        dst_dir = T8_ROOT / rel
        if not dry_run:
            dst_dir.mkdir(parents=True, exist_ok=True)

        for name in filenames:
            src_file = src_dir / name
            ext = src_file.suffix.lower()
            if ext == ".flac" or name.lower() in ("cover.jpg", "folder.jpg") or ext in {".jpg", ".jpeg", ".png"}:
                dst_file = dst_dir / name
                if (not dst_file.exists()
                        or src_file.stat().st_mtime > dst_file.stat().st_mtime):
                    log(f"  COPY: {src_file} -> {dst_file}")
                    if not dry_run:
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
                    if not dry_run:
                        try:
                            dst_file.unlink()
                        except OSError as e:
                            log(f"    [WARN] Could not delete {dst_file}: {e}")

        if not os.listdir(dst_dir):
            log(f"  REMOVE empty dir on T8: {dst_dir}")
            if not dry_run:
                try:
                    dst_dir.rmdir()
                except OSError:
                    pass


def restore_flacs_from_backups(dry_run: bool = False) -> None:
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
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, dest)
                try:
                    backup_file.unlink()
                except OSError as e:
                    log(f"    [WARN] Could not delete backup {backup_file}: {e}")
                    label = album_label_from_dir(dest.parent)
                    add_album_warning_label(label, f"[WARN] Could not delete backup {backup_file}: {e}")

        # Clean up empty directories after restoring files
        if not dry_run and CLEAN_EMPTY_BACKUP_FOLDERS:
            current = backup_dir
            while True:
                try:
                    # Stop at BACKUP_ROOT (don't delete the root itself)
                    if current.resolve() == BACKUP_ROOT.resolve():
                        break
                except FileNotFoundError:
                    break

                try:
                    contents = list(current.iterdir())
                except FileNotFoundError:
                    break

                if contents:
                    # Directory not empty, stop cleaning
                    break

                log(f"  [CLEANUP] Removing empty backup folder: {current}")
                try:
                    current.rmdir()
                except OSError as e:
                    log(f"    [CLEANUP WARN] Could not remove {current}: {e}")
                    break

                # Move up to parent directory
                current = current.parent

    # After all restores, check if BACKUP_ROOT itself is empty (optional cleanup)
    if not dry_run and CLEAN_EMPTY_BACKUP_FOLDERS:
        try:
            if BACKUP_ROOT.exists():
                contents = list(BACKUP_ROOT.iterdir())
                if not contents:
                    log(f"  [CLEANUP] Backup root is empty, but keeping it (may be needed for future backups)")
                    # Note: We don't delete BACKUP_ROOT itself, just log that it's empty
        except Exception as e:
            log(f"  [CLEANUP WARN] Could not check backup root: {e}")

