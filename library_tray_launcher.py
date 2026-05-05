#!/usr/bin/env python3
"""
Tray launcher for library_sync_and_upgrade.py

Cross-platform tray launcher for library_sync_and_upgrade.py
Works on macOS and Windows.

MacOS:
    cd "/Users/christopherhammons/Library/Mobile Documents/com~apple~CloudDocs/scripts"
    source .venv/bin/activate
    python library_tray_launcher.py

    Launched at startup with plist at:
        ~/Library/LaunchAgents/com.music_library.tray.plist

Windows:
    cd C:/Users/docha/iCloudDrive/scripts
    C:/Users/docha/local_python_envs/t8sync/.venv/Scripts/activate
    python library_tray_launcher.py    

    Launched at startup with Task Scheduler

Requires:
    pip install pystray pillow
"""

import os
import platform
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Tuple

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

SYSTEM = platform.system()

# SCRIPT PATHS
# Launcher may live next to main.py OR one level above (legacy "scripts\" with code in .\sync-music-libraries).
_SCRIPTS_HERE = Path(__file__).resolve().parent
_SYNC_NESTED = _SCRIPTS_HERE / "sync-music-libraries"

def _pick_sync_bundle() -> Tuple[Path, Path, Path]:
    """Return (work_dir_for_cwd, main_or_legacy_script, icons_dir)."""
    if (_SCRIPTS_HERE / "main.py").exists():
        return (_SCRIPTS_HERE, _SCRIPTS_HERE / "main.py", _SCRIPTS_HERE / "icons")
    nested_main = _SYNC_NESTED / "main.py"
    if nested_main.exists():
        return (_SYNC_NESTED, nested_main, _SYNC_NESTED / "icons")
    legacy = _SCRIPTS_HERE / "library_sync_and_upgrade.py"
    return (_SCRIPTS_HERE, legacy, _SCRIPTS_HERE / "icons")

SCRIPTS_ROOT, SYNC_SCRIPT, ICON_DIR = _pick_sync_bundle()

# Let the tray import project config (for log path resolution that matches child Python).
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

# Fallback if icons only exist next to launcher
if not ICON_DIR.exists() and (_SCRIPTS_HERE / "icons").exists():
    ICON_DIR = _SCRIPTS_HERE / "icons"

if not SYNC_SCRIPT.exists() and (_SCRIPTS_HERE / "main.py").exists():
    SYNC_SCRIPT = _SCRIPTS_HERE / "main.py"
    SCRIPTS_ROOT = _SCRIPTS_HERE

# Which Python to use to run the sync script
if SYSTEM == "Windows":
    # Your dedicated venv on Windows
    PYTHON_EXE = r"C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe"
    ICON_IDLE_PATH     = ICON_DIR / "pulse_32.png"
    ICON_BUSY_PATH     = ICON_DIR / "pulse-busy_32.png"
    ICON_WARNING_PATH  = ICON_DIR / "pulse-warn_32.png"
    ICON_ERROR_PATH  = ICON_DIR / "pulse-error_32.png"
    
else:
    # On macOS, just reuse the Python that launched this tray
    PYTHON_EXE = sys.executable
    ICON_IDLE_PATH     = ICON_DIR / "pulse_22.png"
    ICON_BUSY_PATH     = ICON_DIR / "pulse-busy_22.png"
    ICON_WARNING_PATH  = ICON_DIR / "pulse-warn_22.png"
    ICON_ERROR_PATH  = ICON_DIR / "pulse-error_22.png"

# Load icons once
icon_idle = Image.open(ICON_IDLE_PATH)
icon_busy = Image.open(ICON_BUSY_PATH)
icon_warning = Image.open(ICON_WARNING_PATH)
icon_error  = Image.open(ICON_ERROR_PATH)

state = {
    "running": False,       # is a sync run currently active?
    "animating": False,     # is the tray icon animation active?
    "last_exit_code": 0,    # exit code of last run
}

tray_icon = None           # pystray.Icon instance
_anim_thread = None        # reference to animation thread


# ---------- Animation helpers ----------

def start_busy_animation():
    """
    Start a single background thread that flashes the tray icon
    between idle and busy while the script is running.
    """
    global _anim_thread

    if state["animating"]:
        # Already animating; don't start another thread
        return

    state["animating"] = True

    def loop():
        frame = 0
        frames = [icon_idle, icon_busy]

        # We guard icon updates and exit cleanly if user quits
        while state["animating"]:
            try:
                if tray_icon is not None:
                    tray_icon.icon = frames[frame % len(frames)]
                    tray_icon.visible = True
                frame += 1
            except Exception:
                # If Windows / pystray gets grumpy during shutdown, just bail out
                break

            time.sleep(0.5)  # flashing speed

        # When animation stops, set final icon state based on last exit code
        try:
            if tray_icon is not None:
                if state["last_exit_code"] == 0:
                    tray_icon.icon = icon_idle
                elif state["last_exit_code"] == 2:
                    tray_icon.icon = icon_warning
                else:
                    tray_icon.icon = icon_error
                tray_icon.visible = True
        except Exception:
            # If icon is already destroyed, ignore
            pass

    _anim_thread = threading.Thread(target=loop, daemon=True)
    _anim_thread.start()


def stop_busy_animation():
    """
    Signal the animation thread to stop. We *don't* join it here,
    to avoid blocking the tray thread; it will exit on its own loop.
    """
    state["animating"] = False


# ---------- Run the sync script ----------

def run_sync(mode="normal", dry=False, checksums=False):
    """
    Kick off library_sync_and_upgrade.py in a background thread.
    """
    if state["running"]:
        # Already running; ignore extra clicks. You could add a notification here.
        return

    state["running"] = True
    state["last_exit_code"] = 0

    # Start flashing icon
    start_busy_animation()

    def worker():
        try:
            args = [PYTHON_EXE, str(SYNC_SCRIPT), "--mode", mode]
            if dry:
                args.append("--dry")
            if checksums:
                args.append("--t8-checksums")

            # cwd = folder containing main.py so imports and paths match a CLI run from that directory.
            work_dir = SYNC_SCRIPT.parent
            env = os.environ.copy()
            # Match config.logs_dir() for the child interpreter (Store Python uses USERPROFILE\.sync…).
            if "SYNC_MUSIC_LOGS_DIR" not in env and SYSTEM == "Windows":
                try:
                    from config import windows_logs_dir_for_executable

                    env["SYNC_MUSIC_LOGS_DIR"] = str(
                        windows_logs_dir_for_executable(Path(PYTHON_EXE))
                    )
                except Exception:
                    try:
                        base = os.environ.get("LOCALAPPDATA", "")
                        if base:
                            env["SYNC_MUSIC_LOGS_DIR"] = str(
                                Path(base) / "sync-music-libraries" / "logs"
                            )
                    except Exception:
                        pass
            proc = subprocess.run(args, cwd=str(work_dir), env=env)
            state["last_exit_code"] = proc.returncode
        except Exception:
            state["last_exit_code"] = 1
        finally:
            state["running"] = False
            # Stop animation; the thread will set final icon
            stop_busy_animation()

    threading.Thread(target=worker, daemon=True).start()


# ---------- Menu callbacks (VERY IMPORTANT: use callables, not direct calls) ----------

def on_run_normal(icon, item):
    run_sync(mode="normal", dry=False)

def on_run_normal_checksums(icon, item):
    run_sync(mode="normal", dry=False, checksums=True)

def on_run_embed(icon, item):
    run_sync(mode="embed", dry=False)

def on_run_restore(icon, item):
    run_sync(mode="restore", dry=False)

def on_run_normal_dry(icon, item):
    run_sync(mode="normal", dry=True)

def on_run_embed_dry(icon, item):
    run_sync(mode="embed", dry=True)

def on_run_restore_dry(icon, item):
    run_sync(mode="restore", dry=True)

def on_quit(icon, item):
    # Stop animation first so we don't update an icon that's being destroyed
    stop_busy_animation()
    # Small delay to let the loop exit cleanly
    time.sleep(0.2)
    icon.stop()


# ---------- Tray setup ----------

def setup_tray():
    global tray_icon

    menu = pystray.Menu(
        #item("Run (normal)", on_run_normal),
        #item("Run (normal-checksums)", on_run_normal_checksums),
        item("Run", on_run_embed),
        # item("Run (restore)", on_run_restore),
        #item("DRY Run (normal)", on_run_normal_dry),
        item("DRY Run", on_run_embed_dry),
        # item("DRY Run (restore)", on_run_restore_dry),
        item("Quit", on_quit),
    )

    tray_icon = pystray.Icon(
        "music_library_sync",
        icon_idle,
        "Music Library Sync",
        menu
    )
    tray_icon.run()


def main():
    setup_tray()


if __name__ == "__main__":
    main()
