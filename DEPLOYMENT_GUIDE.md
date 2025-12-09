# Deployment and Testing Guide

## Quick Start

### Windows

1. **Deploy the refactored code:**
   ```cmd
   deploy_to_icloud.bat
   ```
   This copies all modules to `C:\Users\docha\iCloudDrive\scripts\music-sync-refactored`

2. **Set up test directories (first time only):**
   ```cmd
   setup_test_config.bat
   ```

3. **Reset test environment (when starting fresh):**
   ```cmd
   reset_test_environment.bat
   ```

4. **Test the refactored code:**
   ```cmd
   cd C:\Users\docha\iCloudDrive\scripts\music-sync-refactored
   python test_quick.py
   python main.py --mode normal --dry
   ```

### macOS

1. **Deploy the refactored code:**
   ```bash
   ./deploy_to_icloud.command
   ```
   Or double-click it in Finder.
   
   This copies all modules to `~/Library/Mobile Documents/com~apple~CloudDocs/scripts/music-sync-refactored`

2. **Set up test directories (first time only):**
   ```bash
   ./setup_test_config.command
   ```

3. **Reset test environment (when starting fresh):**
   ```bash
   ./reset_test_environment.command
   ```

4. **Test the refactored code:**
   ```bash
   cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/scripts/music-sync-refactored
   python3 test_quick.py
   python3 main.py --mode normal --dry
   ```

## Test Workflow

### Initial Setup (One Time)

1. Run `setup_test_config.bat` (Windows) or `setup_test_config.command` (macOS)
   - Creates all necessary test directories

### For Each Test Cycle

1. **Reset environment (optional, for clean slate):**
   - Windows: `reset_test_environment.bat`
   - macOS: `reset_test_environment.command`
   - ⚠️ **WARNING**: This deletes all files in test directories!

2. **Copy test albums:**
   - Copy a few albums from `E:\Plex Library\Music` (Windows) or your iTunes library (macOS)
   - Paste into `C:\Users\docha\Downloads\Music` (Windows) or `~/Downloads/Music` (macOS)

3. **Run dry-run test:**
   ```bash
   cd [deployed-folder]
   python main.py --mode normal --dry
   ```
   - Review the output
   - Check what would be done

4. **Run actual sync (if dry-run looks good):**
   ```bash
   python main.py --mode normal
   ```

5. **Verify results:**
   - Check files in `D:\TestMusicLibrary\ROON\Music` (Windows)
   - Check files in `[iCloud]/TestMusicLibrary/ROON/Music` (macOS)
   - Check T8 sync: `D:\TestMusicLibrary\T8\Music` (Windows)
   - Review summary log

## Directory Structure

### Windows Test Directories
```
C:\Users\docha\Downloads\Music          # Source: Copy albums here
D:\TestMusicLibrary\ROON\Music         # Target: Organized library
D:\TestMusicLibrary\T8\Music            # T8 sync destination
D:\TestMusicLibrary\_EmbeddedArtOriginal # FLAC backups
D:\TestMusicLibrary\_UpdateOverlay      # Update patches
```

### macOS Test Directories
```
~/Downloads/Music                       # Source: Copy albums here
~/Library/Mobile Documents/.../TestMusicLibrary/ROON/Music  # Target
~/Library/Mobile Documents/.../TestMusicLibrary/T8/Music    # T8 sync
~/Library/Mobile Documents/.../TestMusicLibrary/_EmbeddedArtOriginal  # Backups
~/Library/Mobile Documents/.../TestMusicLibrary/_UpdateOverlay        # Updates
```

## Deployment Location

The refactored code is deployed to:
- **Windows**: `C:\Users\docha\iCloudDrive\scripts\music-sync-refactored`
- **macOS**: `~/Library/Mobile Documents/com~apple~CloudDocs/scripts/music-sync-refactored`

This keeps it separate from your existing scripts in the parent `scripts` folder.

## Comparing with Original

To verify the refactored version works the same:

1. **Test original script:**
   ```bash
   cd C:\Users\docha\iCloudDrive\scripts
   python library_sync_and_upgrade.py --mode normal --dry > original.log
   ```

2. **Test refactored script:**
   ```bash
   cd C:\Users\docha\iCloudDrive\scripts\music-sync-refactored
   python main.py --mode normal --dry > refactored.log
   ```

3. **Compare logs:**
   - Review both logs side-by-side
   - They should produce similar output (may have minor formatting differences)

## Troubleshooting

### Import Errors
- Make sure dependencies are installed: `pip install -r requirements.txt`
- Check Python path: Use `python3` on macOS, `python` on Windows

### Path Issues
- Verify paths in `config.py` match your setup
- Check that directories exist (run `setup_test_config`)

### Permission Errors
- On macOS, you may need to grant Terminal/iTerm full disk access
- Check iCloud Drive sync status

### Module Not Found
- Make sure you're in the deployed folder when running
- Verify all `.py` files were copied (check deployment folder)

## Next Steps After Testing

1. ✅ Verify refactored code works correctly
2. ✅ Compare with original script output
3. 🔄 Merge in your other refactored script (if different)
4. 🔄 Re-test after merge
5. 💬 Discuss log architecture improvements
6. 🐛 Fix any bugs found during testing

