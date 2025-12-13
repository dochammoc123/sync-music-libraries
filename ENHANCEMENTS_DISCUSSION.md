# Enhancement Discussion

## 1. Duplicate Detection Using Checksum

### Current Behavior:
- No duplicate detection currently implemented
- Files are moved/renamed based on tags
- No checksum comparison

### Proposed Enhancement:
- After normalizing filename, calculate checksum (MD5 or SHA256)
- Check if file with same checksum already exists in album directory
- Warn if duplicate detected
- Options:
  - Skip duplicate file
  - Replace existing file
  - Keep both (rename with suffix)

### Questions:
- Should we check duplicates within the same album only, or across entire library?
- What checksum algorithm? (MD5 faster, SHA256 more secure)
- Should we store checksums in a database/cache for faster lookups?

## 2. Files Without Tags - What Do We Do Now?

### Current Behavior:
- Files without tags use path-based fallback to extract artist/album
- Track number extracted from filename
- Title extracted from filename (with artist prefix removed)
- Tags are written later in Step 4 (after backup)

### Questions:
- What should happen if a file has NO tags and we can't determine album/artist from path?
- Should we skip the file? Move to "Unknown" folder? Warn and continue?
- What if we can't extract track number from filename?
- Should we require minimum metadata (at least artist + album) before moving files?

### Current Flow:
1. Try to read tags from file
2. If no tags, use path-based fallback (Downloads/Artist/Album structure)
3. If path-based fails, use MusicBrainz verification
4. If all fails, use "Unknown Artist" / "Unknown Album"
5. Files are moved and tags written later

## 3. Bitrate-Based Upgrade Instead of FLAC-Only

### Current Behavior:
- `upgrade_albums_to_flac_only()` assumes FLAC is always best
- If FLAC exists in album, deletes all other formats (MP3, M4A, etc.)
- No bitrate checking

### Proposed Enhancement:
- Check bitrate of existing files
- Only upgrade if new file has higher bitrate
- Keep existing file if it has better quality
- Compare:
  - FLAC (lossless) > any lossy format
  - Higher bitrate lossy > lower bitrate lossy
  - Same format: keep higher bitrate

### Questions:
- How to get bitrate from different formats?
  - FLAC: check `audio.info.bitrate` or `audio.info.bits_per_sample`
  - MP3: `audio.info.bitrate`
  - M4A: `audio.info.bitrate`
- What if bitrate is same but one is lossless and one is lossy?
- Should we prefer lossless even if bitrate is lower?

### Current Code Location:
- `file_operations.py::upgrade_albums_to_flac_only()`
- Currently just checks if `.flac` exists, then deletes other formats

## 4. Update Overlay Folder Review

### Current Behavior (from `sync_operations.py::apply_updates_from_overlay()`):
1. Scans `UPDATE_ROOT` recursively
2. For audio files: copies to `MUSIC_ROOT`, removes backup for that path
3. For other files (cover.jpg, etc.): copies to `MUSIC_ROOT`
4. Deletes source files from `UPDATE_ROOT` after copying
5. Returns list of updated album directories

### Questions:
- Should we check for duplicates before copying from UPDATE_ROOT?
- Should we verify tags/bitrate before overwriting existing files?
- What if UPDATE_ROOT file has lower quality than existing file?
- Should UPDATE_ROOT files always win (overwrite), or should we be smarter?

### Current Design:
- UPDATE_ROOT is meant for "patch" files - new originals, updated artwork
- Files are treated as authoritative (always overwrite)
- No quality/bitrate checking
- No duplicate detection

## Next Steps

1. **Duplicate Detection**: Implement checksum-based duplicate detection
2. **Tag-less Files**: Define policy for files with no tags
3. **Bitrate Checking**: Replace FLAC-only logic with bitrate comparison
4. **Update Overlay**: Review and enhance with duplicate/quality checks

