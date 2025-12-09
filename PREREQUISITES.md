# Prerequisites and Setup

## Python Installation

### Windows

1. **Download Python:**
   - Go to https://www.python.org/downloads/
   - Download Python 3.11 or later (3.12 recommended)
   - ⚠️ **Important**: Check "Add Python to PATH" during installation

2. **Verify Installation:**
   ```cmd
   python --version
   ```
   Should show Python 3.x.x

3. **Install pip (if not included):**
   ```cmd
   python -m ensurepip --upgrade
   ```

### macOS

1. **Check if Python is installed:**
   ```bash
   python3 --version
   ```

2. **If not installed, use Homebrew (recommended):**
   ```bash
   # Install Homebrew if needed
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   
   # Install Python
   brew install python3
   ```

3. **Or download from python.org:**
   - Go to https://www.python.org/downloads/
   - Download macOS installer
   - Run the installer

## Virtual Environment Setup

### Windows

1. **Create virtual environment:**
   ```cmd
   cd C:\Users\docha\local_python_envs
   python -m venv t8sync
   ```

2. **Activate virtual environment:**
   ```cmd
   C:\Users\docha\local_python_envs\t8sync\Scripts\activate
   ```
   
   Or use the full path in scripts:
   ```cmd
   C:\Users\docha\local_python_envs\t8sync\Scripts\activate.bat
   ```

3. **Verify activation:**
   - Your prompt should show `(t8sync)` at the beginning
   - Run: `python --version` to confirm

4. **Install dependencies:**
   ```cmd
   pip install -r requirements.txt
   ```

### macOS

1. **Create virtual environment:**
   ```bash
   cd ~/local_python_envs
   python3 -m venv t8sync
   ```

2. **Activate virtual environment:**
   ```bash
   source ~/local_python_envs/t8sync/bin/activate
   ```

3. **Verify activation:**
   - Your prompt should show `(t8sync)` at the beginning
   - Run: `python3 --version` to confirm

4. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Required Dependencies

Install from `requirements.txt`:

```bash
# Windows (with venv activated)
pip install -r requirements.txt

# macOS (with venv activated)
pip3 install -r requirements.txt
```

### Core Dependencies:
- `mutagen` - Audio metadata reading/writing
- `musicbrainzngs` - MusicBrainz API client
- `requests` - HTTP library for web art fetching

### Optional (for tray launcher):
- `pystray` - System tray icon
- `Pillow` - Image processing

## Virtual Environment Location

**Windows:**
```
C:\Users\docha\local_python_envs\t8sync
```

**macOS:**
```
~/local_python_envs/t8sync
```

## Activating Before Running Scripts

### Windows (Command Prompt)
```cmd
C:\Users\docha\local_python_envs\t8sync\Scripts\activate
```

### Windows (PowerShell)
```powershell
C:\Users\docha\local_python_envs\t8sync\Scripts\Activate.ps1
```

### macOS (Terminal)
```bash
source ~/local_python_envs/t8sync/bin/activate
```

## Quick Setup Scripts

### Windows
Create a batch file to activate and run:
```cmd
@echo off
C:\Users\docha\local_python_envs\t8sync\Scripts\activate
cd C:\Users\docha\iCloudDrive\scripts\music-sync-refactored
python main.py --mode normal --dry
pause
```

### macOS
Create a shell script:
```bash
#!/bin/bash
source ~/local_python_envs/t8sync/bin/activate
cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/scripts/music-sync-refactored
python3 main.py --mode normal --dry
```

## Troubleshooting

### "python is not recognized" (Windows)
- Python not in PATH - reinstall Python with "Add to PATH" checked
- Or use full path: `C:\Users\docha\AppData\Local\Programs\Python\Python3xx\python.exe`

### "python3: command not found" (macOS)
- Install Python 3 via Homebrew or python.org
- May need to use `python3` instead of `python`

### Virtual environment activation fails
- Verify the venv directory exists
- Recreate if needed: `python -m venv t8sync` (Windows) or `python3 -m venv t8sync` (macOS)

### Import errors after activation
- Make sure you activated the correct venv
- Reinstall dependencies: `pip install -r requirements.txt`
- Check that you're using the venv's Python: `which python` (macOS) or `where python` (Windows)

### Permission errors (macOS)
- May need to grant Terminal full disk access in System Preferences
- Check iCloud Drive sync status

## Verification Checklist

- [ ] Python 3.11+ installed
- [ ] Virtual environment created
- [ ] Virtual environment activates successfully
- [ ] Dependencies installed (`pip list` shows mutagen, musicbrainzngs, requests)
- [ ] Can import modules: `python -c "import mutagen; print('OK')"`
- [ ] Scripts can find Python in venv

