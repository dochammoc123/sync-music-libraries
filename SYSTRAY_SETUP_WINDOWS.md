# Windows Systray Setup Guide

## Quick Start

### Manual Start (Testing)
1. Run `start_tray_windows.bat` from the repo, or
2. Manually:
   ```cmd
   C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\activate
   cd C:\Users\docha\iCloudDrive\scripts
   python library_tray_launcher.py
   ```

### Automatic Start (Task Scheduler)

#### Option 1: Using Task Scheduler GUI
1. Open **Task Scheduler** (search for it in Start menu)
2. Click **Create Basic Task** (right side)
3. **Name**: "Music Library Sync Tray"
4. **Trigger**: "When I log on"
5. **Action**: "Start a program"
6. **Program/script**: `C:\Users\docha\iCloudDrive\scripts\start_tray_windows.bat`
   - Or use the full path to Python: `C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe`
   - **Arguments**: `C:\Users\docha\iCloudDrive\scripts\library_tray_launcher.py`
   - **Start in**: `C:\Users\docha\iCloudDrive\scripts`
7. Check **"Open the Properties dialog for this task when I click Finish"**
8. In Properties:
   - **General tab**: Check "Run whether user is logged on or not" (optional)
   - **General tab**: Check "Run with highest privileges" (if needed)
   - **Settings tab**: Check "Allow task to be run on demand"
   - **Settings tab**: Check "Run task as soon as possible after a scheduled start is missed"

#### Option 2: Using PowerShell (Advanced)
```powershell
$action = New-ScheduledTaskAction -Execute "C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe" -Argument "C:\Users\docha\iCloudDrive\scripts\library_tray_launcher.py" -WorkingDirectory "C:\Users\docha\iCloudDrive\scripts"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName "Music Library Sync Tray" -Action $action -Trigger $trigger -Principal $principal -Description "Music Library Sync System Tray Launcher"
```

## Tray Launcher Configuration

The tray launcher (`library_tray_launcher.py`) is configured to:
- ✅ Look for `main.py` first (refactored version)
- ✅ Fall back to `library_sync_and_upgrade.py` if `main.py` doesn't exist
- ✅ Use venv Python: `C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe`
- ✅ Find icons in `icons/` directory relative to script location

## Testing the Tray Launcher

1. **Test manually first**:
   ```cmd
   cd C:\Users\docha\iCloudDrive\scripts
   C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\activate
   python library_tray_launcher.py
   ```

2. **Check for tray icon**:
   - Look in system tray (bottom right, may be hidden behind ^)
   - Should see pulse icon
   - Right-click to see menu

3. **Test menu options**:
   - Run (normal) - should start sync
   - DRY Run (normal) - should do dry-run
   - Quit - should close tray

4. **Check for errors**:
   - If icon doesn't appear, check console output
   - Verify all dependencies installed: `pip list | findstr pystray`
   - Verify icons exist: `dir icons\pulse_32.png`

## Troubleshooting

### Tray Icon Doesn't Appear
- Check if Python is running: Task Manager → Details → python.exe
- Check console output for errors
- Verify pystray is installed: `pip install pystray pillow`
- Try running from command line to see errors

### Script Not Found
- Verify `main.py` or `library_sync_and_upgrade.py` exists in scripts folder
- Check that icons directory exists
- Verify paths in `library_tray_launcher.py` are correct

### Task Scheduler Issues
- Make sure task is enabled
- Check task history for errors
- Verify "Start in" directory is correct
- Try running task manually from Task Scheduler

### Venv Issues
- Verify venv exists: `dir C:\Users\docha\local_python_envs\t8sync\.venv`
- Verify Python executable: `dir C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe`
- Recreate venv if needed: `python -m venv C:\Users\docha\local_python_envs\t8sync\.venv`

## Next Steps After Setup

1. ✅ Verify tray launcher starts automatically on login
2. ✅ Test all menu options work
3. ✅ Verify sync script runs correctly from tray
4. ✅ Check that icons change during sync (idle → busy → idle/warning/error)
5. ✅ Test notifications appear after sync completes

