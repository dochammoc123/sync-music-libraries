# Enhancement Discussion & Design Clarification

## Focus: Bug Fixes & Documentation
**Goal**: Understand current state, add comments/doc where needed, not changing too much of existing design.

---

## 1. Duplicate Detection Using Checksum

### Current Behavior:
- No duplicate detection currently implemented
- Files are moved/renamed based on tags
- UPDATE_ROOT files are copied without filename normalization

### Design Intent:
- **UPDATE_ROOT**: Maybe normalize filenames to ensure no duplicates
- Checksum wouldn't hurt but maybe not necessary
- Primary concern: prevent duplicate files in same album directory

### Action:
- Consider adding filename normalization in UPDATE_ROOT processing
- Optional: Add checksum check as safety measure
- Focus: Same album directory duplicate detection

---

## 2. Files Without Tags - Current Logic

### Current Behavior:
- Files without tags use path-based fallback to extract artist/album
- Track number extracted from filename
- Title extracted from filename (with artist prefix removed)
- Tags are written later in Step 4 (after backup)
- If path-based fails, uses MusicBrainz verification
- If all fails, uses "Unknown Artist" / "Unknown Album"

### Design Decision:
- **Leave current logic alone** - it works as designed
- Current flow is acceptable for handling files without tags

### Current Flow:
1. Try to read tags from file
2. If no tags, use path-based fallback (Downloads/Artist/Album structure)
3. If path-based fails, use MusicBrainz verification
4. If all fails, use "Unknown Artist" / "Unknown Album"
5. Files are moved and tags written later

---

## 3. FLAC-Only Upgrade Logic

### Current Behavior:
- `upgrade_albums_to_flac_only()` assumes FLAC is always best
- If FLAC exists in album, deletes all other formats (MP3, M4A, etc.)
- No bitrate checking

### Design Intent:
- Current logic is **okay as-is**
- Should test when upgrading/updating MP3 to non-FLAC (even another MP3)
- Maybe assumptions about bitrate are okay
- Most music file updates come from downloads folder, not UPDATE_ROOT

### Action:
- Keep current FLAC-only logic
- Test edge cases (MP3 → MP3 upgrades)
- Document assumptions

### Current Code Location:
- `file_operations.py::upgrade_albums_to_flac_only()`
- Currently checks if `.flac` exists, then deletes other formats

---

## 4. Update Overlay Folder - Design Intent

### Primary Purpose:
- **Allow embedding new artwork into selected albums**
- No way to drop a JPG by itself into downloads (without album folder) and know what album on ROON to update/embed
- UPDATE_ROOT maintains structure synced with ROON, so you can drop `cover.jpg` into the correct album path

### Secondary Function:
- **Direct overlay of music files** (audio files)
- Audio filenames are normalized using tags (same as downloads) to ensure consistency
- Later step removes MP3 if FLAC exists (FLAC-only upgrade)
- Most music file updates come from downloads folder
- UPDATE_ROOT mainly used for isolated art updates

### Current Behavior (from `sync_operations.py::apply_updates_from_overlay()`):
1. Scans `UPDATE_ROOT` recursively
2. For audio files: normalizes filename using tags, copies to `MUSIC_ROOT`, removes backup for that path
3. For other files (cover.jpg, etc.): copies to `MUSIC_ROOT`
4. Deletes source files from `UPDATE_ROOT` after copying
5. Returns list of updated album directories
6. Warns if destination file exists (for future bitrate upgrade feature)

### Future Enhancement - Frequency/Sample Rate Comparison:
- **Compare sample rates (frequency)** when same filename/ext detected
- Only compare within same extension (e.g., FLAC to FLAC, not FLAC to MP3)
- Warn user if overwriting with lower sample rate
- Ask for confirmation before overwrite
- Assumption: FLAC is lossless (always preferred over other formats)
- Within same format: prefer higher sample rate (e.g., 96kHz > 44.1kHz)
- Example: Two purchases of same album, one 96kHz FLAC, one 44.1kHz FLAC → warn and ask
- Uses same logic as downloads: both normalize filenames and overwrite existing files

---

## 5. Corrupted Files Handling

### Current Behavior:
- Corrupted files (can't read tags/art) are left as-is
- Tag writing and art embedding skip corrupted files gracefully
- New downloads with same filename will overwrite corrupted files (via `shutil.move()`)
- Format detection tries multiple formats if extension doesn't match

### Design Intent:
- **Leave corrupted files as-is** - can't be fixed if truly corrupted
- **Allow new downloads to replace** - if same filename detected, new file overwrites old
- **Don't try to fix** - corrupted files are skipped during tag/art operations

### Current Code Location:
- `tag_operations.py::write_tags_to_file()` - tries multiple formats, logs warning if all fail
- `artwork.py::embed_missing_art_global()` - tries multiple formats, skips if can't read
- `file_operations.py::move_album_from_downloads()` - uses `shutil.move()` which overwrites existing files

---

## Summary

1. **Duplicate Detection**: Filenames normalized in UPDATE_ROOT, future bitrate upgrade feature
2. **Tag-less Files**: Leave current logic alone - it works
3. **FLAC-only Logic**: Keep as-is, test edge cases
4. **Update Overlay**: Filenames now normalized, future bitrate upgrade feature documented
5. **Corrupted Files**: Left as-is, new downloads can overwrite

**Focus**: Bug fixes, documentation, understanding current state.

