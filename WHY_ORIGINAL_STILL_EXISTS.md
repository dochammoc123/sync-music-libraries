# Why library_sync_and_upgrade.py Still Exists

## Purpose

The original `library_sync_and_upgrade.py` (1269 lines) is preserved for:

1. **Backward Compatibility**: Existing run scripts (normal_run.bat, safe_test_run.bat, etc.) still reference it
2. **Reference**: Useful for comparing behavior during testing
3. **Safety**: Can fall back to original if refactored version has issues
4. **Gradual Migration**: Allows testing new version while keeping old one available

## Current Status

- ✅ **Refactored version**: `main.py` + modules (ready for testing)
- ✅ **Original version**: `library_sync_and_upgrade.py` (still works)
- ✅ **Tray launcher**: Updated to try `main.py` first, falls back to original

## Migration Plan

### Phase 1: Testing (Current)
- Test refactored version (`main.py`)
- Compare output with original
- Fix any issues found

### Phase 2: Update Run Scripts (After Testing)
Once refactored version is verified:
- Update `normal_run.bat` to use `main.py`
- Update `safe_test_run.bat` to use `main.py`
- Update `restore_originals.bat` to use `main.py`
- Update `embed_art.bat` to use `main.py`
- Update corresponding `.command` files for macOS

### Phase 3: Optional Cleanup (Future)
After everything works:
- Can optionally remove `library_sync_and_upgrade.py`
- Or keep it as backup/reference

## Current Run Scripts

These scripts currently reference the original:
- `normal_run.bat` / `normal_run.command`
- `safe_test_run.bat` / `safe_test_run.command`
- `restore_originals.bat` / `restore_originals.command`
- `embed_art.bat` / `embed_art.command`

**Note**: The tray launcher (`library_tray_launcher.py`) already uses `main.py` first, then falls back to the original if `main.py` doesn't exist.

