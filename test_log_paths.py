#!/usr/bin/env python3
"""
Verify log directories and files are created the same way as main.py.

Run from the deploy folder (with venv Python):

  python test_log_paths.py

Tray runs set SYNC_MUSIC_LOGS_DIR only on the child process; this script mirrors a normal
CLI run unless you set SYNC_MUSIC_LOGS_DIR yourself:

  set SYNC_MUSIC_LOGS_DIR=D:\\somewhere\\logs
  python test_log_paths.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    la = os.environ.get("LOCALAPPDATA", "(missing)")
    print("sys.executable:", sys.executable)
    print("sys.base_executable:", getattr(sys, "base_executable", "(n/a)"))
    print("LOCALAPPDATA:", repr(la))
    print("SYNC_MUSIC_LOGS_DIR:", repr(os.environ.get("SYNC_MUSIC_LOGS_DIR", "(not set)")))
    print()

    from config import DETAIL_LOG_FILE, LOGS_DIR, STRUCTURED_SUMMARY_LOG_FILE, windows_logs_dir_for_executable

    print(
        "windows_logs_dir_for_executable(this interpreter):",
        windows_logs_dir_for_executable(Path(sys.executable)),
    )

    print("LOGS_DIR:", LOGS_DIR)
    print("DETAIL_LOG_FILE:", DETAIL_LOG_FILE)
    print("STRUCTURED_SUMMARY_LOG_FILE:", STRUCTURED_SUMMARY_LOG_FILE)
    try:
        print("LOGS_DIR.resolve():", LOGS_DIR.resolve())
        print("DETAIL_LOG_FILE.resolve():", DETAIL_LOG_FILE.resolve())
    except OSError as e:
        print("(resolve failed:", e, ")")
    print("DETAIL path repr (copy-paste into cmd):", repr(str(DETAIL_LOG_FILE)))
    print()

    from structured_logging import logmsg, setup_detail_logging

    setup_detail_logging()

    logmsg.info("test_log_paths: INFO line (detail log + console)")
    logmsg.verbose(
        "test_log_paths: VERBOSE line (detail log only - not printed on console)"
    )

    ok = True
    if not LOGS_DIR.is_dir():
        print("FAIL: LOGS_DIR is not a directory:", LOGS_DIR)
        ok = False
    else:
        print("OK: LOGS_DIR exists")

    if DETAIL_LOG_FILE is None:
        print("FAIL: DETAIL_LOG_FILE is None")
        ok = False
    elif not DETAIL_LOG_FILE.is_file():
        print("FAIL: detail log file missing:", DETAIL_LOG_FILE)
        ok = False
    else:
        sz = DETAIL_LOG_FILE.stat().st_size
        print("OK: detail log file size:", sz, "bytes")
        if sz == 0:
            print("WARN: detail log is 0 bytes (unexpected after logging)")
            ok = False

    if STRUCTURED_SUMMARY_LOG_FILE is None:
        print("WARN: STRUCTURED_SUMMARY_LOG_FILE is None")
    elif STRUCTURED_SUMMARY_LOG_FILE.is_file():
        print(
            "OK: summary log path exists (touched at startup):",
            STRUCTURED_SUMMARY_LOG_FILE.stat().st_size,
            "bytes",
        )
    else:
        print("WARN: summary file missing:", STRUCTURED_SUMMARY_LOG_FILE)

    # Prove on-disk content from Python (same process that wrote the file).
    if DETAIL_LOG_FILE is not None and DETAIL_LOG_FILE.is_file():
        try:
            raw = DETAIL_LOG_FILE.read_text(encoding="utf-8", errors="replace")
            print()
            print("Read-back from Python (first 400 chars, proves file is readable here):")
            print(raw[:400])
            if "[VERBOSE]" not in raw and "VERBOSE" not in raw:
                print("WARN: expected a VERBOSE line in detail log for this test.")
        except OSError as e:
            print("Read-back failed:", e)
            ok = False

    # What cmd.exe sees (same machine; catches junction oddities).
    print()
    try:
        p = subprocess.run(
            ["cmd.exe", "/c", "dir", "/b", str(LOGS_DIR)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        listing = (p.stdout or "").strip()
        if listing:
            print("cmd.exe: dir /b LOGS_DIR ->")
            print(listing)
        else:
            print("cmd.exe: dir /b LOGS_DIR -> (empty) stderr:", (p.stderr or "").strip())
    except Exception as e:
        print("cmd.exe dir failed:", e)

    try:
        se = Path(sys.executable).resolve().as_posix().lower()
        if "pythonsoftwarefoundation" in se or "windowsapps" in se:
            print()
            print(
                "Note: Microsoft Store Python redirects AppData\\Local file I/O; "
                "this project uses USERPROFILE\\.sync-music-libraries\\logs for that build."
            )
    except Exception:
        pass

    print()
    if ok:
        print("PASS - open this file in Explorer or Notepad:")
        print(" ", DETAIL_LOG_FILE)
        print("You should see both INFO and [VERBOSE] lines for test_log_paths.")
        print()
        print("Open the log from a shell (pick ONE that matches your terminal):")
        print("  CMD.exe (path matches DETAIL_LOG_FILE above):")
        print('    type "' + str(DETAIL_LOG_FILE) + '"')
        print("  PowerShell:")
        print('    Get-Content "' + str(DETAIL_LOG_FILE) + '"')
    else:
        print("FAIL - fix permissions or paths above.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
