# Testing Plan for Refactored Music Library Sync

## Overview
Test the refactored modular code to ensure it works identically to the original script before merging any additional refactored code.

## Test Environment Setup

### Prerequisites
- Python virtual environment activated
- Dependencies installed: `pip install -r requirements.txt`
- Test music library paths configured in `config.py`
- Backup of original script available

### Test Modes
1. **Dry Run Tests** - No file modifications, just verify logic
2. **Small Scale Tests** - Test with a few albums
3. **Full Integration Tests** - Test complete workflows

## Test Checklist

### Phase 1: Basic Functionality (Dry Run)

#### 1.1 Command Line Interface
- [ ] `python main.py --mode normal --dry` runs without errors
- [ ] `python main.py --mode embed --dry` runs without errors
- [ ] `python main.py --mode restore --dry` runs without errors
- [ ] All command-line arguments work correctly
- [ ] Error messages are clear and helpful

#### 1.2 Module Imports
- [ ] All modules import without errors
- [ ] No circular import issues
- [ ] Type hints don't cause runtime errors

#### 1.3 Configuration
- [ ] Paths are correctly resolved on both Windows and macOS
- [ ] iCloud directory detection works
- [ ] All config constants are accessible

### Phase 2: Core Operations (Dry Run)

#### 2.1 Downloads Processing
- [ ] Scans downloads directory correctly
- [ ] Groups files by album correctly
- [ ] Detects audio file types correctly
- [ ] Handles missing tags gracefully
- [ ] Creates correct album directory structure
- [ ] Handles multi-disc albums correctly

#### 2.2 Tag Operations
- [ ] Reads tags from FLAC files correctly
- [ ] Reads tags from MP3 files correctly
- [ ] Handles missing/empty tags correctly
- [ ] Year selection algorithm works correctly
- [ ] Filename formatting is correct

#### 2.3 Artwork Operations
- [ ] Finds pre-downloaded artwork correctly
- [ ] Exports embedded artwork correctly
- [ ] Web art lookup works (if enabled)
- [ ] Creates cover.jpg and folder.jpg correctly
- [ ] Handles missing artwork gracefully

#### 2.4 File Operations
- [ ] Album directory creation works
- [ ] File moving logic is correct
- [ ] Cleanup of download directories works
- [ ] FLAC-only enforcement logic is correct

#### 2.5 Sync Operations
- [ ] Update overlay processing works
- [ ] T8 sync logic is correct
- [ ] Backup operations work correctly
- [ ] Restore operations work correctly

### Phase 3: Integration Tests (Small Scale)

#### 3.1 Normal Mode - Full Workflow
- [ ] Process 1-2 test albums from downloads
- [ ] Verify files moved correctly
- [ ] Verify artwork created correctly
- [ ] Verify FLAC-only cleanup works
- [ ] Verify T8 sync works (if configured)
- [ ] Check summary log is created
- [ ] Check notifications work

#### 3.2 Embed Mode
- [ ] Test embedding artwork from UPDATE overlay
- [ ] Verify backups are created
- [ ] Verify FLACs are updated correctly

#### 3.3 Restore Mode
- [ ] Test restoring from backups
- [ ] Verify backups are deleted after restore
- [ ] Verify T8 sync after restore

### Phase 4: Comparison with Original

#### 4.1 Side-by-Side Comparison
- [ ] Run original script with `--dry` on test data
- [ ] Run new `main.py` with `--dry` on same test data
- [ ] Compare log outputs - should be identical
- [ ] Compare summary logs - should be identical

#### 4.2 Actual File Operations (Small Test)
- [ ] Create test download folder with 1 album
- [ ] Run original script (normal mode)
- [ ] Reset test environment
- [ ] Run new `main.py` (normal mode)
- [ ] Compare results - files should be in same locations
- [ ] Compare artwork - should be identical

## Test Data Preparation

### Minimal Test Set
1. **Test Album 1**: Single disc, has embedded artwork
2. **Test Album 2**: Multi-disc, has pre-downloaded cover.jpg
3. **Test Album 3**: No artwork, should fetch from web
4. **Test Album 4**: Mixed formats (FLAC + MP3), should keep FLAC only

### Test Directory Structure
```
test_downloads/
  ├── Artist1 - Album1/
  │   ├── 01 - Track1.flac
  │   ├── 02 - Track2.flac
  │   └── cover.jpg
  └── Artist2 - Album2/
      ├── CD1/
      │   ├── 01 - Track1.flac
      │   └── 02 - Track2.flac
      └── CD2/
          ├── 01 - Track1.flac
          └── 02 - Track2.flac
```

## Running Tests

### Quick Test (Dry Run)
```bash
# Test normal mode
python main.py --mode normal --dry

# Test embed mode  
python main.py --mode embed --dry

# Test restore mode
python main.py --mode restore --dry
```

### Comparison Test
```bash
# Original script
python library_sync_and_upgrade.py --mode normal --dry > original.log

# New script
python main.py --mode normal --dry > new.log

# Compare (on Linux/Mac)
diff original.log new.log

# Or use a diff tool
```

## Known Issues to Watch For

1. **Path Handling**: Windows vs macOS path differences
2. **File Locking**: Files may be locked by media players
3. **Network Issues**: Web art lookup may fail
4. **Large Libraries**: Performance on very large libraries
5. **Unicode**: Special characters in artist/album names

## Success Criteria

- [ ] All dry-run tests pass
- [ ] Small-scale integration tests pass
- [ ] Output matches original script
- [ ] No new errors introduced
- [ ] All modules work independently
- [ ] Type hints don't cause issues

## Next Steps After Testing

1. Fix any issues found
2. Merge in the other refactored script (if different)
3. Re-test after merge
4. Discuss log architecture improvements
5. Fix bugs identified during testing

