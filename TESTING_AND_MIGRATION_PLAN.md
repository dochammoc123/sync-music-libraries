# Testing and Logging Migration Plan

**Date:** 2026-01-25  
**Status:** In Progress

## Current State

### ✅ Completed
1. **Refactoring**: Monolithic script split into 7 modules
2. **Structured Logging API**: New `structured_logging.py` module created with `logmsg` API
3. **Partial Migration**: Some modules partially migrated to structured logging:
   - `main.py` - Mixed (uses both old and new)
   - `file_operations.py` - Partially migrated
   - `tag_operations.py` - Partially migrated  
   - `sync_operations.py` - Partially migrated (Step 2 overlay/embed)

### ⏳ In Progress
1. **Testing**: Step 2 overlay/embed functionality needs testing
2. **Logging Migration**: Many modules still use old `log()` function

### ❌ Not Started
1. **Complete Logging Migration**: Several modules still need full migration
2. **Cleanup**: Remove old logging code after migration complete

## Testing Priority: Step 2 Overlay/Embed

### What to Test
1. **UPDATE Overlay Processing** (`sync_operations.py::apply_updates_from_overlay()`)
   - Audio files copied from UPDATE_ROOT to MUSIC_ROOT
   - Filename normalization using tags
   - Artwork files (cover.jpg) copied and normalized
   - Artist artwork normalized to folder.jpg
   - Album artwork normalized to cover.jpg
   - Files deleted from UPDATE_ROOT after copying
   - Frequency/sample rate comparison for upgrades
   - File size comparison for upgrades

2. **Artwork Embedding from Updates** (`main.py` Step 2 + Step 4)
   - When `EMBED_FROM_UPDATES` is True
   - Albums with new cover.jpg from UPDATE_ROOT get artwork embedded
   - Backups created before embedding
   - FLACs updated with new artwork

### Test Steps
```bash
# 1. Dry-run test
python main.py --mode embed --dry

# 2. Small test with real files
# - Place test cover.jpg in UPDATE_ROOT/Artist/(Year) Album/
# - Place test audio file in UPDATE_ROOT/Artist/(Year) Album/
# - Run: python main.py --mode embed

# 3. Verify:
# - Files copied correctly
# - Artwork normalized correctly
# - Artwork embedded into FLACs
# - Backups created
# - UPDATE_ROOT cleaned up
# - Logging output is correct
```

## Logging Migration Status

### Modules Needing Migration

#### 1. `artwork.py` - **HIGH PRIORITY** (Step 2/4 related)
- **Status**: Uses old `log()` function throughout
- **Lines to migrate**: ~50+ log() calls
- **Key functions**:
  - `fetch_art_from_web()` - web art lookup
  - `embed_art_into_flacs()` - embedding artwork
  - `embed_missing_art_global()` - global missing art fixup
  - `fixup_missing_art()` - final fixup

#### 2. `sync_operations.py` - **HIGH PRIORITY** (Step 2)
- **Status**: Mixed - has structured logging but also many old log() calls
- **Lines to migrate**: ~30+ log() calls remaining
- **Key functions**:
  - `remove_backup_for()` - backup removal
  - `apply_updates_from_overlay()` - **CRITICAL FOR STEP 2** (partially migrated)
  - `sync_update_root_structure()` - directory sync
  - `sync_music_to_t8()` - T8 sync
  - `sync_backups()` - backup sync
  - `restore_flacs_from_backups()` - restore mode

#### 3. `file_operations.py` - **MEDIUM PRIORITY**
- **Status**: Partially migrated (has structured logging in some places)
- **Lines to migrate**: ~20+ log() calls remaining
- **Key functions**:
  - `cleanup_download_directories()` - cleanup operations
  - `move_album_from_downloads()` - file moving

#### 4. `tag_operations.py` - **MEDIUM PRIORITY**
- **Status**: Partially migrated (has structured logging in some places)
- **Lines to migrate**: ~15+ log() calls remaining
- **Key functions**:
  - `get_tags()` - tag reading warnings
  - `group_files_by_album()` - grouping logic

#### 5. `main.py` - **MEDIUM PRIORITY**
- **Status**: Mixed - uses both old and new logging
- **Lines to migrate**: ~30+ log() calls remaining
- **Key areas**:
  - Disk capacity checks
  - Step headers
  - Error handling

#### 6. `config.py` - **LOW PRIORITY**
- **Status**: Uses logger directly (may be fine as-is)
- **Lines**: ~5 logger.debug/warning calls
- **Note**: May not need migration (debug/info logging)

## Migration Strategy

### For Each Module:

1. **Identify log() calls** - Find all `log()` function calls
2. **Set context** - Determine album/item context needed
3. **Convert to structured logging**:
   - `log("message")` → `logmsg.info("message")` (if no context)
   - `log("message", label=...)` → Set album context, then `logmsg.info("message")`
   - `log("[WARN] ...")` → `logmsg.warn("...")`
   - `log("[ERROR] ...")` → `logmsg.error("...")`
4. **Test** - Verify logging output is correct
5. **Remove old log() import** - Once all migrated

### Structured Logging Patterns

```python
# Simple info message (no context)
log("Starting operation...")
→ logmsg.info("Starting operation...")

# Album-level message
label = album_label_from_dir(album_dir)
log(f"Processing: {label}")
→ 
album_key = logmsg.set_album(album_dir)
try:
    logmsg.info("Processing album")
finally:
    logmsg.unset_album(album_key)

# Item-level message (with album context)
log(f"  Processing file: {filename}")
→
item_key = logmsg.set_item(str(filename))
try:
    logmsg.info("Processing file: %item%")
finally:
    logmsg.unset_item(item_key)

# Warning/Error
log(f"[WARN] Something went wrong: {error}")
→ logmsg.warn("Something went wrong: {error}", error=str(error))
```

## Step-by-Step Plan

### Phase 1: Test Step 2 (Overlay/Embed) - **DO THIS FIRST**
1. ✅ Create test files in UPDATE_ROOT
2. ✅ Run dry-run test
3. ✅ Verify logging output
4. ✅ Run real test with small dataset
5. ✅ Verify all functionality works

### Phase 2: Migrate Step 2 Logging
1. ✅ Complete migration of `sync_operations.py::apply_updates_from_overlay()`
2. ✅ Migrate `artwork.py::embed_art_into_flacs()` (used in Step 2)
3. ✅ Test Step 2 again after migration

### Phase 3: Migrate Remaining Logging
1. ✅ Migrate `artwork.py` completely
2. ✅ Complete `sync_operations.py` migration
3. ✅ Complete `file_operations.py` migration
4. ✅ Complete `tag_operations.py` migration
5. ✅ Complete `main.py` migration

### Phase 4: Testing After Migration
1. ✅ Run full test suite
2. ✅ Compare logs with original script
3. ✅ Verify summary logs are correct
4. ✅ Fix any issues found

### Phase 5: Cleanup
1. ✅ Remove old `log()` function from `logging_utils.py`
2. ✅ Remove `ALBUM_SUMMARY` and `GLOBAL_WARNINGS` if no longer needed
3. ✅ Update imports across codebase
4. ✅ Final testing

## Known Issues

1. **sync_operations.py lines 144-165**: Logic error in frequency comparison (missing elif, wrong indentation)
2. **Mixed logging**: Some functions use both old and new logging (inconsistent)

## Success Criteria

- [ ] Step 2 overlay/embed tested and working
- [ ] All modules migrated to structured logging
- [ ] All tests pass
- [ ] Logging output is clean and consistent
- [ ] Old logging code removed
- [ ] Documentation updated

## Next Immediate Steps

1. **Test Step 2** with dry-run first
2. **Fix bug in sync_operations.py** (lines 144-165)
3. **Complete migration of sync_operations.py::apply_updates_from_overlay()**
4. **Migrate artwork.py** (critical for Step 2)
5. **Re-test Step 2** after migration

