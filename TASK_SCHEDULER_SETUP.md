# Task Scheduler Setup for Systray Launcher

## Finding Your Existing Task

If you had a Task Scheduler task before and can't find it:

### Option 1: PowerShell Search
```powershell
# Run in PowerShell
.\find_tray_task.ps1
```

This will search for any tasks with "music", "tray", "library", or "sync" in the name.

### Option 2: Manual Search in Task Scheduler
1. Open **Task Scheduler** (Win+R → `taskschd.msc`)
2. Look in:
   - **Task Scheduler Library** (root)
   - **Task Scheduler Library → Microsoft → Windows** (sometimes tasks get created here)
3. Check **Task Status** column - disabled tasks won't show in "Active tasks"
4. Use **View → Show Hidden Tasks** to see all tasks

### Option 3: Check All Tasks
```powershell
# List all tasks
Get-ScheduledTask | Select-Object TaskName, State, TaskPath | Sort-Object TaskName
```

## Creating a New Task

### Quick Method: PowerShell Script (Recommended)
1. **Run PowerShell as Administrator**:
   - Right-click PowerShell → "Run as Administrator"
   
2. **Navigate to script directory**:
   ```powershell
   cd C:\src\sync-music-libraries
   ```

3. **Run the creation script**:
   ```powershell
   .\create_tray_task.ps1
   ```

This will create a task named "Music Library Sync Tray" that:
- Runs `start_tray_windows.bat` at logon
- Runs as your user account
- Has highest privileges
- Will restart if it fails

### Manual Method: Task Scheduler GUI

1. **Open Task Scheduler** (Win+R → `taskschd.msc`)

2. **Create Basic Task**:
   - Click "Create Basic Task" (right side)
   - Name: `Music Library Sync Tray`
   - Description: `Music Library Sync System Tray Launcher`

3. **Trigger**:
   - Select "When I log on"
   - Click Next

4. **Action**:
   - Select "Start a program"
   - Program/script: `C:\Users\docha\iCloudDrive\scripts\start_tray_windows.bat`
   - **OR** use Python directly:
     - Program/script: `C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe`
     - Arguments: `C:\Users\docha\iCloudDrive\scripts\library_tray_launcher.py`
     - Start in: `C:\Users\docha\iCloudDrive\scripts`

5. **Finish**:
   - Check "Open the Properties dialog for this task when I click Finish"
   - Click Finish

6. **Properties** (important settings):
   - **General tab**:
     - ✅ Check "Run whether user is logged on or not" (optional)
     - ✅ Check "Run with highest privileges"
     - ✅ Select "Configure for: Windows 10"
   
   - **Conditions tab**:
     - ✅ Uncheck "Start the task only if the computer is on AC power" (if you want it on battery)
     - ✅ Check "Start the task only if the following network connection is available" (optional)
   
   - **Settings tab**:
     - ✅ Check "Allow task to be run on demand"
     - ✅ Check "Run task as soon as possible after a scheduled start is missed"
     - ✅ Check "If the task fails, restart every: 1 minute" (up to 3 times)
     - ✅ Check "If the running task does not end when requested, force it to stop"

7. **Click OK**

## Testing the Task

### Test Immediately
```powershell
# In PowerShell
Start-ScheduledTask -TaskName "Music Library Sync Tray"
```

### Check if it's running
1. Open Task Manager (Ctrl+Shift+Esc)
2. Look for `python.exe` or `library_tray_launcher.py` in Processes
3. Check system tray for the pulse icon

### View Task History
1. Open Task Scheduler
2. Find your task
3. Click on it
4. Click "History" tab at bottom
5. Look for errors or successful runs

## Troubleshooting

### Task Not Running
- Check task is **Enabled** (not Disabled)
- Check **Last Run Result** - should be 0x0 (success)
- Check **History** tab for errors
- Verify the script path is correct
- Try running the script manually first

### Task Runs But Tray Doesn't Appear
- Check if Python is running in Task Manager
- Check console output (if running from batch file)
- Verify pystray and Pillow are installed
- Check icons directory exists

### Task Disappears
- Check "Show Hidden Tasks" in View menu
- Tasks might be in a subfolder
- Check if task was deleted (won't show in history)

## Alternative: Startup Folder

If Task Scheduler doesn't work, you can use the Startup folder:

1. Press **Win+R**
2. Type: `shell:startup`
3. Press Enter
4. Create a shortcut to `start_tray_windows.bat`
5. The tray will start when you log in

## Next Steps

After setting up the task:
1. ✅ Reboot or log out/in to test
2. ✅ Verify tray icon appears
3. ✅ Test menu options work
4. ✅ Verify sync runs correctly from tray

