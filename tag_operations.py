"""
Tag operations for reading and processing audio file metadata.
"""
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3

# So we can set album artist (TPE2) when NORMALIZE_ARTIST_IN_TAGS is True
try:
    EasyID3.RegisterTextKey("albumartist", "TPE2")
except Exception:
    pass  # Already registered or unsupported
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
import musicbrainzngs

from config import AUDIO_EXT, ENABLE_WEB_ART_LOOKUP, MB_APP, MB_VER, MB_CONTACT, NORMALIZE_ALBUM_IN_TAGS, NORMALIZE_ARTIST_IN_TAGS, WEB_ART_LOOKUP_TIMEOUT
# log() removed - use structured_logging logmsg for console/detail output


def find_audio_files(root: Path) -> Iterator[Path]:
    """Generator that yields all audio files under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in AUDIO_EXT:
                yield p


def get_tags_from_path(path: Path, downloads_root: Path) -> Dict[str, Any]:
    """
    Fallback: Extract basic info from file path when tags can't be read.
    Assumes structure like: downloads_root/Artist/Album/track.flac
    """
    try:
        rel = path.relative_to(downloads_root)
        parts = list(rel.parts)
        
        # Extract artist and album from path
        if len(parts) >= 2:
            artist = parts[0]
            second = parts[1]
            # File directly in artist folder (e.g. Artist/song.flac) -> no album
            if Path(second).suffix.lower() in AUDIO_EXT:
                album = "Unknown Album"
            else:
                album = second
        elif len(parts) == 1:
            artist = "Unknown Artist"
            album = "Unknown Album"
        else:
            artist = "Unknown Artist"
            album = "Unknown Album"
        
        # Extract title and track number from filename
        import re
        title = path.stem
        
        # Try to extract track number from filename like "02 - " or "02."
        tracknum = 0
        track_match = re.match(r'^(\d+)\s*[-.]\s*', title)
        if track_match:
            try:
                tracknum = int(track_match.group(1))
            except ValueError:
                tracknum = 0
            # Remove track number prefix
            title = re.sub(r'^\d+\s*[-.]\s*', '', title).strip()
        
        # Try to remove artist prefix like "Lorde - " or "Artist - "
        # This handles cases like "02 - Lorde - 400 Lux" -> "400 Lux"
        # Pattern: "Artist - Title" format (after tracknum removed)
        title = re.sub(r'^[^-]+-\s*', '', title).strip()  # Remove "Artist - " prefix
        if not title:
            title = path.stem
        
        return {
            "artist": artist.strip(),
            "album": album.strip(),
            "year": "",
            "tracknum": tracknum,
            "discnum": 1,
            "title": title.strip(),
        }
    except Exception:
        # Fallback to minimal info
        return {
            "artist": "Unknown Artist",
            "album": "Unknown Album",
            "year": "",
            "tracknum": 0,
            "discnum": 1,
            "title": path.stem,
        }


def get_sample_rate(audio_path: Path) -> Optional[int]:
    """
    Get the sample rate (frequency) in Hz from an audio file.
    Returns None if cannot be determined.
    """
    try:
        audio = MutagenFile(str(audio_path))
        if audio is None:
            return None
        
        # Most formats expose sample_rate via .info
        if hasattr(audio, 'info') and hasattr(audio.info, 'sample_rate'):
            return int(audio.info.sample_rate)
        
        return None
    except Exception:
        return None


def get_bitrate(audio_path: Path) -> Optional[int]:
    """
    Get the bitrate in bits per second from an audio file.
    Returns None if cannot be determined.
    Note: For lossless formats (FLAC), this is the actual encoded bitrate, not sample rate.
    """
    try:
        audio = MutagenFile(str(audio_path))
        if audio is None:
            return None
        
        if hasattr(audio, 'info') and hasattr(audio.info, 'bitrate'):
            # bitrate is typically in bps (bits per second)
            return int(audio.info.bitrate)
        
        return None
    except Exception:
        return None


def get_audio_duration(audio_path: Path) -> Optional[float]:
    """
    Get the audio duration in seconds from an audio file.
    Returns None if cannot be determined.
    Note: This is metadata duration, which may be incorrect for truncated files.
    """
    try:
        audio = MutagenFile(str(audio_path))
        if audio is None:
            return None
        
        if hasattr(audio, 'info') and hasattr(audio.info, 'length'):
            return float(audio.info.length)
        
        return None
    except Exception:
        return None


def estimate_expected_file_size(duration: float, sample_rate: int, channels: int = 2, format: str = "flac", bitrate: Optional[int] = None) -> Optional[int]:
    """
    Estimate expected file size for an audio file based on duration, sample rate, format, and actual bitrate.
    Returns estimated size in bytes, or None if cannot estimate.
    
    If bitrate is provided, uses that directly (most accurate).
    Otherwise falls back to format-based estimates.
    
    For FLAC: Uses actual bitrate if available, otherwise estimates based on compression ratio
    For MP3/M4A/AAC: Uses actual bitrate if available, otherwise uses format defaults
    """
    if duration <= 0:
        return None
    
    # If we have actual bitrate, use it directly (most accurate)
    if bitrate and bitrate > 0:
        # bitrate is in bits per second, convert to bytes: duration * bitrate / 8
        estimated = int(duration * bitrate / 8)
        return estimated
    
    # Fall back to format-based estimates if no bitrate available
    if format.lower() == "flac":
        if sample_rate <= 0:
            return None
        # FLAC: uncompressed ≈ duration * sample_rate * channels * 3 bytes (24-bit)
        uncompressed = duration * sample_rate * channels * 3
        # FLAC compression ratio typically 0.5-0.7, use 0.6 as average
        estimated = int(uncompressed * 0.6)
        return estimated
    elif format.lower() in ("mp3", "m4a", "aac"):
        # Lossy formats: estimate based on typical bitrates
        # MP3: 128-320 kbps, use 192 kbps as average
        # M4A/AAC: similar, use 256 kbps as average
        default_bitrate = 192000 if format.lower() == "mp3" else 256000  # bits per second
        estimated = int(duration * default_bitrate / 8)
        return estimated
    
    return None


def check_file_size_warning(audio_path: Path) -> Optional[Tuple[str, str]]:
    """
    Check if file size seems unusually small for its duration/quality.
    Returns (level, message) tuple if suspicious, None otherwise.
    Level is "WARN" (likely truncated) or "INFO" (suspicious); thresholds are format-dependent.
    
    Uses expected bitrate based on sample rate/format (NOT actual file bitrate,
    since truncated files will have artificially low bitrates).
    
    Note: This is a heuristic - actual file size can vary significantly.
    - WARN: File is < 70% (FLAC) or < 85% (lossy) of expected (may be truncated)
    - INFO: File is 70-85% (FLAC) or 85-96% (lossy) of expected (suspicious but not certain)
    
    Future enhancement: To truly detect truncated files, we could attempt to decode
    the last second of audio using ffmpeg/pydub. This would require:
    - Additional dependency (ffmpeg or pydub)
    - Slower processing (decoding each file)
    - More reliable detection of truncation
    
    For now, file size comparison when duplicates exist is the most practical approach.
    """
    try:
        file_size = audio_path.stat().st_size
        duration = get_audio_duration(audio_path)
        sample_rate = get_sample_rate(audio_path)
        bitrate = get_bitrate(audio_path)  # Get actual bitrate from file
        
        if not duration:
            return None
        
        # Get format from extension
        ext = audio_path.suffix.lower()
        format_name = ext[1:] if ext else "flac"  # Remove the dot
        
        # Get channels (default to 2 if can't determine)
        channels = 2
        try:
            audio = MutagenFile(str(audio_path))
            if audio and hasattr(audio, 'info') and hasattr(audio.info, 'channels'):
                channels = audio.info.channels
        except Exception:
            pass
        
        # Calculate expected bitrate based on format
        # For lossy formats (MP3, M4A, AAC): use actual bitrate if available (more accurate)
        # For lossless formats (FLAC): use expected bitrate based on sample rate
        # Truncated files may have incorrect bitrate metadata, but for lossy formats,
        # using actual bitrate is more accurate than assuming all MP3s are 320kbps
        expected_bitrate = None
        if format_name == "flac" and sample_rate:
            # FLAC: estimate based on sample rate (higher sample rate = higher bitrate)
            # Typical FLAC bitrates: 44.1kHz ≈ 800-1000 kbps, 96kHz ≈ 2000-3000 kbps, 192kHz ≈ 4000-6000 kbps
            if sample_rate >= 192000:
                expected_bitrate = 5000000  # ~5 Mbps for 192kHz
            elif sample_rate >= 96000:
                expected_bitrate = 2500000  # ~2.5 Mbps for 96kHz
            elif sample_rate >= 44100:
                expected_bitrate = 900000   # ~900 kbps for 44.1kHz
            else:
                # Fallback: calculate from sample rate
                expected_bitrate = int(sample_rate * channels * 24 * 0.6)
        elif format_name in ("mp3", "m4a", "aac"):
            # Lossy formats: prefer actual bitrate if available (MP3s can be 128kbps, 192kbps, 256kbps, 320kbps, VBR, etc.)
            # Only fall back to typical bitrates if actual bitrate can't be determined
            if bitrate and bitrate > 0:
                expected_bitrate = bitrate
            else:
                # Fallback: use typical bitrates (MP3: 320kbps, M4A/AAC: 256kbps)
                expected_bitrate = 320000 if format_name == "mp3" else 256000
        
        # Use expected bitrate to detect truncation
        expected_size = estimate_expected_file_size(duration, sample_rate, channels, format_name, expected_bitrate)
        if not expected_size:
            return None
        
        # Check size ratio and return appropriate warning level
        # Thresholds vary by format:
        # - FLAC: Compression varies significantly (50–70% typical); many valid files are 80–90%
        #   WARN: < 70% of expected (likely truncated)
        #   INFO: 70–85% of expected (suspicious but may be normal compression variation)
        #   Above 85%: no message (normal)
        # - Lossy formats (MP3, M4A, AAC): Bitrate is more predictable
        #   WARN: < 85% of expected (likely truncated)
        #   INFO: 85-96% of expected (suspicious - may be missing end)
        size_ratio = file_size / expected_size if expected_size > 0 else 1.0
        
        # Set thresholds based on format
        if format_name == "flac":
            warn_threshold = 0.70  # 70% for FLAC (compression varies a lot; only warn when clearly short)
            info_threshold = 0.85  # 85% for FLAC (above this is normal variation)
        else:
            warn_threshold = 0.85  # 85% for lossy formats (bitrate is more predictable)
            info_threshold = 0.96  # 96% for lossy formats
        
        if size_ratio < info_threshold:
            bitrate_str = f" @ {expected_bitrate/1000:.0f}kbps expected" if expected_bitrate else f" @ {sample_rate}Hz" if sample_rate else ""
            message = f"File size ({file_size:,} bytes) is {size_ratio*100:.0f}% of expected ({expected_size:,} bytes) for {duration:.1f}s{bitrate_str} - may be truncated or corrupted (long silent sections compress well and can cause false positives)"
            if size_ratio < warn_threshold:
                return ("WARN", message)
            elif size_ratio < info_threshold:
                return ("INFO", message)
        
        return None
    except Exception:
        return None


def get_tags(path: Path, downloads_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Return tags dict from a file: artist, album, year, tracknum, discnum, title.
    Returns None if tags cannot be read. Does NOT use path-based fallback here -
    that decision is made at the directory level in group_by_album().
    """
    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None or not audio.tags:
            return None
    except Exception as e:
        # File might be corrupted, wrong format, or unreadable
        # Log warning but return None - path-based fallback will be handled at directory level
        # log() removed - use structured_logging logmsg for console/detail output
        from config import DOWNLOADS_DIR, MUSIC_ROOT
        
        # Determine if this is a new file in downloads (WARN) or existing file in music root (INFO)
        is_in_downloads = False
        try:
            path_resolved = path.resolve()
            downloads_resolved = DOWNLOADS_DIR.resolve()
            # Check if path is within downloads directory
            try:
                path.relative_to(downloads_resolved)
                is_in_downloads = True
            except ValueError:
                # Path is not relative to downloads, check string comparison as fallback
                path_str = str(path_resolved)
                downloads_str = str(downloads_resolved)
                # Normalize paths for comparison (handle both forward and backslashes)
                path_normalized = path_str.replace("\\", "/").lower()
                downloads_normalized = downloads_str.replace("\\", "/").lower()
                if path_normalized.startswith(downloads_normalized):
                    is_in_downloads = True
        except Exception:
            # If path resolution fails, try string comparison as fallback
            path_str = str(path).replace("\\", "/").lower()
            downloads_str = str(DOWNLOADS_DIR).replace("\\", "/").lower()
            if downloads_str in path_str or path_str.startswith(downloads_str):
                is_in_downloads = True
        
        # New corrupt files in downloads are a problem (WARN)
        # Existing corrupt files in music root will be overwritten (INFO)
        is_warning = is_in_downloads
        
        try:
            from structured_logging import logmsg
            msg = f"Could not read tags from {str(path)}: {str(e)}"
            if logmsg.current_album_label is not None:
                if is_warning:
                    # Log as warning for corrupt files in downloads (new files being processed)
                    logmsg.warn(msg)
                else:
                    # Log as info for corrupt files in music root (will be overwritten)
                    logmsg.info(msg)
            else:
                # Log as verbose when no album context (appears in detail log only, not console)
                logmsg.verbose(msg)
        except Exception:
            pass  # Fallback if structured logging not available
        
        # Error already logged via logmsg if available
        return None

    try:
        def _get(tag: str, default: str = "") -> str:
            v = audio.tags.get(tag)
            return v[0] if v else default

        artist = _get("albumartist") or _get("artist") or "Unknown Artist"
        album = _get("album") or "Unknown Album"

        date = _get("date") or _get("year") or ""
        year = date[:4] if len(date) >= 4 and date[:4].isdigit() else ""

        trackno = _get("tracknumber") or _get("TRACKNUMBER") or "0"
        discno = _get("discnumber") or _get("DISCNUMBER") or "1"
        title = _get("title") or path.stem

        try:
            tracknum = int(trackno.split("/")[0])
        except ValueError:
            tracknum = 0

        try:
            discnum = int(discno.split("/")[0])
        except ValueError:
            discnum = 1

        # Raw albumartist from file (may be missing); FLAC uses ALBUMARTIST, ID3 uses albumartist
        raw_albumartist = (_get("albumartist") or _get("ALBUMARTIST") or "").strip()

        return {
            "artist": artist.strip(),
            "album": album.strip(),
            "year": year.strip(),
            "tracknum": tracknum,
            "discnum": discnum,
            "title": title.strip(),
            "albumartist": raw_albumartist or None,
        }
    except Exception as e:
        # Error reading tags even though file opened
        # log() removed - use structured_logging logmsg for console/detail output
        try:
            from structured_logging import logmsg
            # Only log warning if album context is set (during processing, not during scanning)
            # This prevents duplicate warnings and ensures they appear only under album context
            msg = f"Error processing tags from {str(path)}: {str(e)}"
            if logmsg.current_album_label is not None:
                # Log as warning when we have album context (appears in summary)
                logmsg.warn(msg)
            else:
                # Log as verbose when no album context (appears in detail log only, not console)
                logmsg.verbose(msg)
        except Exception:
            pass  # Fallback if structured logging not available
        return None


def verify_album_via_musicbrainz(artist: str, album: str) -> Optional[Tuple[str, str]]:
    """
    Query MusicBrainz to verify/identify an album.
    Returns (verified_artist, verified_album) if found, None otherwise.
    Handles "Various Artists" compilations.
    """
    if not ENABLE_WEB_ART_LOOKUP:
        return None
    
    try:
        # Initialize MusicBrainz if not already done
        try:
            musicbrainzngs.set_useragent(MB_APP, MB_VER, MB_CONTACT)
        except Exception:
            pass  # Already initialized
        
        # Search for the release
        result = musicbrainzngs.search_releases(
            artist=artist,
            release=album,
            limit=5  # Get a few results to find best match
        )
        
        releases = result.get("release-list", [])
        if not releases:
            return None
        
        # Use the first result (most relevant)
        release = releases[0]
        
        # Get artist credit (handles "Various Artists" and collaborations)
        artist_credit_list = release.get("artist-credit", [])
        if artist_credit_list:
            # For compilations, artist-credit might be empty or have "Various Artists"
            # Check if it's a compilation
            if release.get("release-group", {}).get("secondary-type-list"):
                secondary_types = release["release-group"]["secondary-type-list"]
                if any(st.get("secondary-type") == "Compilation" for st in secondary_types):
                    verified_artist = "Various Artists"
                else:
                    # Get primary artist from credit
                    artist_name = artist_credit_list[0].get("name", artist)
                    verified_artist = artist_name
            else:
                # Regular album - get primary artist
                artist_name = artist_credit_list[0].get("name", artist)
                verified_artist = artist_name
        else:
            # No artist credit - might be compilation
            verified_artist = "Various Artists"
        
        verified_album = release.get("title", album)
        
        return (verified_artist, verified_album)
        
    except Exception as e:
        return None


def normalize_unicode_canonical(s: str) -> str:
    """
    Normalize Unicode to a canonical form for grouping and folder names:
    NFD decomposition then remove combining characters (accents).
    E.g. "Céline Dion" and "Celine Dion" both become "Celine Dion".
    """
    if not s:
        return s
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


# Canonicalization for album-level artist names.
# Used to collapse "oddball" bucket artists (like "Christmas Music") into
# a consistent Various Artists bucket so compilations don't scatter under
# different pseudo-artist folders.
GENERIC_COMPILATION_ARTISTS = {
    "christmas music",
    "holiday music",
    "christmas songs",
    "holiday songs",
    "soundtrack",
    "soundtracks",
    "various",
    "compilation",
    "compilations",
}


def normalize_album_artist(artist: str) -> str:
    """
    Normalize album-level artist names for folder/label purposes.
    - Collapse generic bucket names (e.g. "Christmas Music") into "Various Artists"
      so that compilations don't end up under arbitrary pseudo-artist folders.
    - Normalize accents (e.g. "Céline Dion" -> "Celine Dion") so variants
      are grouped under one folder.
    """
    a = (artist or "").strip()
    if a.lower() in GENERIC_COMPILATION_ARTISTS:
        return "Various Artists"
    return normalize_unicode_canonical(a)


# For two-artist albums: only file under the majority artist if they have at least this share
# of tracks (e.g. 2/3). Otherwise treat as Various Artists (e.g. 50/50 or 5/3).
MAJORITY_ARTIST_MIN_RATIO = 2 / 3


def normalize_album_name(album: str) -> str:
    """
    Normalize album name for grouping and folder naming so multi-disc sets with
    inconsistent tags merge into one album (e.g. one folder with CD1/CD2 subdirs).
    Strips common disc suffixes: "(Disc 1)", "[Disc 2]", " (1/2)", " [2/2]", " - Disc 1", etc.
    """
    if not album or not isinstance(album, str):
        return album or ""
    s = album.strip()
    # Strip trailing disc patterns (repeat until no change so we handle "Album (Disc 1) (1/2)")
    while True:
        orig = s
        # (Disc N), [Disc N], (disc N), [disc N]
        s = re.sub(r'\s*[(\[]\s*disc\s*\d+\s*[)\]]\s*$', '', s, flags=re.IGNORECASE)
        # (N/M), [N/M] e.g. (1/2), [2/2]
        s = re.sub(r'\s*[(\[]\s*\d+\s*/\s*\d+\s*[)\]]\s*$', '', s)
        # - Disc N, – Disc N
        s = re.sub(r'\s*[-–—]\s*disc\s*\d+\s*$', '', s, flags=re.IGNORECASE)
        s = s.strip()
        if s == orig:
            break
    return s


def choose_album_artist_album(items: List[Tuple[Path, Dict[str, Any]]], verify_via_mb: bool = True) -> Tuple[str, str]:
    """
    Given a list of (path, tags) for files in the same directory, pick canonical
    artist and album values similar to choose_album_year.
    
    Strategy:
      1. Collect all artist/album pairs from files that have tags.
      2. Find the most common (artist, album) pair.
      3. If tags exist, use them directly (they already handle Various Artists correctly).
      4. If can't determine albumDir from most used tag (all tags are missing):
         - Use path-based fallback to extract artist/album from folder structure
         - Verify via MusicBrainz (for Various Artists detection and verification)
      5. Last resort: use path-based fallback or "Unknown Artist/Album".
    
    Returns (artist, album) tuple.
    """
    # Collect artist/album from files with tags; normalize album so "Album (Disc 1)" and "Album [Disc 2]" merge
    artist_album_pairs = [(t["artist"], normalize_album_name(t["album"])) for (_p, t) in items if t.get("artist") and t.get("album")]
    
    if artist_album_pairs:
        # Find most common (artist, album) pair
        counts = Counter(artist_album_pairs)
        max_count = max(counts.values())
        candidates = [pair for pair, c in counts.items() if c == max_count]
        candidate_artist, candidate_album = candidates[0]
        
        # Tie (e.g. 50/50 two artists): treat as compilation so we don't arbitrarily pick one
        if len(candidates) > 1:
            return ("Various Artists", candidate_album)
        # Many distinct track artists (e.g. soundtrack with no albumartist): compilation
        distinct_artists = len(set(normalize_album_artist(a) for (a, _) in artist_album_pairs))
        if distinct_artists >= 3:
            return ("Various Artists", candidate_album)
        # Two artists: only use majority if they have at least 2/3 of tracks (e.g. 6/8 ok, 5/3 → Various)
        total_tracks = len(artist_album_pairs)
        if distinct_artists == 2 and max_count < total_tracks * MAJORITY_ARTIST_MIN_RATIO:
            return ("Various Artists", candidate_album)
        
        normalized_artist = normalize_album_artist(candidate_artist)
        return (normalized_artist, candidate_album)  # candidate_album already normalized
    
    # Can't determine albumDir from most used tag (all tags are missing)
    # Use path-based fallback, then verify via MusicBrainz (for Various Artists detection)
    if items:
        first_path = items[0][0]
        fallback_tags = get_tags_from_path(first_path, first_path.parent.parent.parent)
        if fallback_tags:
            path_artist = fallback_tags["artist"]
            path_album = fallback_tags["album"]
            
            # Try MusicBrainz verification before using path-based values
            # This is where we detect Various Artists when tags don't exist
            if verify_via_mb and path_artist != "Unknown Artist" and path_album != "Unknown Album":
                verified = verify_album_via_musicbrainz(path_artist, path_album)
                if verified:
                    verified_artist, verified_album = verified
                    normalized_artist = normalize_album_artist(verified_artist)
                    return (normalized_artist, normalize_album_name(verified_album))
                # else: no MusicBrainz match, using path values (already logged via logmsg if available)
            
            normalized_artist = normalize_album_artist(path_artist)
            return (normalized_artist, normalize_album_name(path_album))
    
    # Last resort
    return ("Unknown Artist", "Unknown Album")


def find_root_album_directory(file_path: Path, all_files: List[Path], downloads_root: Optional[Path] = None) -> Path:
    """
    Find the root album directory for a file.
    
    The root album directory is the first directory (walking up from the file)
    that contains music files, but never DOWNLOADS_DIR itself.
    This allows us to treat files in subdirectories (like "originals") as if they
    were in the parent directory.
    
    Important: DOWNLOADS_DIR is never treated as an album folder, even if it
    contains music files directly (e.g., from browser downloads).
    
    Example:
      - File: Downloads/Music/Lorde/Pure Heroine/originals/track.flac
      - If Lorde/Pure Heroine/ contains music files, return Lorde/Pure Heroine/
      - If file is directly in Downloads/Music/, return Downloads/Music/ (but this
        should be handled separately as files without album structure)
    """
    current = file_path.parent
    root_dir = current
    
    # Never treat downloads_root itself as an album directory
    if downloads_root and current.resolve() == downloads_root.resolve():
        # File is directly in downloads root - return it as-is (will be handled separately)
        return current
    
    # Walk up the directory tree (but stop before reaching downloads_root)
    while True:
        # Check if we've reached downloads root - stop before it
        if downloads_root:
            try:
                if current.resolve() == downloads_root.resolve():
                    break
            except (FileNotFoundError, OSError):
                break
        
        # Check if this directory contains any music files (other than the current file)
        dir_has_music = any(
            f.parent.resolve() == current.resolve() and f != file_path
            for f in all_files
        )
        
        if dir_has_music:
            root_dir = current
        
        # Stop if we can't go higher
        try:
            parent = current.parent
            if parent == current:  # Reached filesystem root
                break
            # Stop if next parent would be downloads_root
            if downloads_root:
                try:
                    if parent.resolve() == downloads_root.resolve():
                        break
                except (FileNotFoundError, OSError):
                    pass
            current = parent
        except (ValueError, AttributeError):
            break
    
    return root_dir


def group_by_album(files: List[Path], downloads_root: Optional[Path] = None) -> Dict[Tuple[str, str], List[Tuple[Path, Dict[str, Any]]]]:
    """
    Group paths into albums by (artist, album) ONLY.
    Year is still read from tags but not used as part of the key.
    
    Strategy:
      1. Find the root album directory for each file (first directory containing music files)
         This treats files in subdirectories (like "originals") as if they were in the parent
      2. Group files by their root album directory
      3. For each directory group, determine artist/album from files with tags
         (using most common value, similar to choose_album_year)
      4. If can't determine from tags (all tags missing), use path-based fallback
         and verify via MusicBrainz (for Various Artists detection)
      5. For files without tags, use the determined artist/album
      6. Group all files by the determined (artist, album) key
    
    Returns dict mapping (artist, album) -> list of (path, tags) tuples.
    """
    # Step 1: Find root album directory for each file and group by root directory
    files_by_dir: Dict[Path, List[Path]] = {}
    for f in files:
        root_dir = find_root_album_directory(f, files, downloads_root)
        files_by_dir.setdefault(root_dir, []).append(f)
    
    # Step 2: For each directory, get tags and determine artist/album
    all_items: List[Tuple[Path, Dict[str, Any]]] = []
    dir_to_key: Dict[Path, Tuple[str, str]] = {}
    
    for dir_path, dir_files in files_by_dir.items():
        # Special case: if dir_path is downloads_root, files are directly in downloads
        # (e.g., from browser downloads). These will be grouped by tags only.
        if downloads_root and dir_path.resolve() == downloads_root.resolve():
            # Files directly in downloads root (no album folder structure) - already logged via logmsg if available
            pass
        
        # Get tags for all files in this directory
        items_with_tags: List[Tuple[Path, Dict[str, Any]]] = []
        items_without_tags: List[Path] = []
        
        for f in dir_files:
            tags = get_tags(f, downloads_root)
            if tags:
                items_with_tags.append((f, tags))
            else:
                items_without_tags.append(f)
        
        # Determine artist/album from files with tags (with MusicBrainz verification)
        if items_with_tags:
            artist, album = choose_album_artist_album(items_with_tags, verify_via_mb=True)
            dir_to_key[dir_path] = (artist, album)
            
            # Add files with tags
            all_items.extend(items_with_tags)
            
            # For files without tags, create minimal tags using determined artist/album
            for f in items_without_tags:
                try:
                    from structured_logging import logmsg
                    # Use format() for placeholder replacement since these are called before album context is set
                    msg = f"No tags for {str(f)}, using artist/album from other files in directory: {artist} - {album}"
                    logmsg.verbose(msg)
                except Exception:
                    pass  # Fallback if structured logging not available
                # Create minimal tags with determined artist/album
                fallback_tags = get_tags_from_path(f, downloads_root if downloads_root else f.parent.parent.parent)
                if fallback_tags:
                    fallback_tags["artist"] = artist
                    fallback_tags["album"] = album
                    all_items.append((f, fallback_tags))
                else:
                    # Last resort
                    all_items.append((f, {
                        "artist": artist,
                        "album": album,
                        "year": "",
                        "tracknum": 0,
                        "discnum": 1,
                        "title": f.stem,
                    }))
        else:
            # Can't determine albumDir from most used tag (all tags are missing)
            # Use path-based fallback, then verify via MusicBrainz
            if dir_files:
                first_file = dir_files[0]
                fallback_tags = get_tags_from_path(first_file, downloads_root if downloads_root else first_file.parent.parent.parent)
                if fallback_tags:
                    path_artist = fallback_tags["artist"]
                    path_album = fallback_tags["album"]
                    
                    # Verify via MusicBrainz before using path-based values
                    verified = verify_album_via_musicbrainz(path_artist, path_album)
                    if verified:
                        artist, album = verified
                        try:
                            from structured_logging import logmsg
                            msg = f"No tags in directory {str(dir_path)}, MusicBrainz verified: {path_artist} - {path_album} -> {artist} - {album}"
                            logmsg.verbose(msg)
                        except Exception:
                            pass  # Fallback if structured logging not available
                    else:
                        artist, album = path_artist, path_album
                        try:
                            from structured_logging import logmsg
                            msg = f"No tags in directory {str(dir_path)}, using path-based: {artist} - {album}"
                            logmsg.verbose(msg)
                        except Exception:
                            pass  # Fallback if structured logging not available

                    artist = normalize_album_artist(artist)
                    dir_to_key[dir_path] = (artist, album)
                    
                    for f in dir_files:
                        tags = get_tags_from_path(f, downloads_root if downloads_root else f.parent.parent.parent)
                        if tags:
                            tags["artist"] = artist
                            tags["album"] = album
                            all_items.append((f, tags))
    
    # Step 3: Group all items by (artist, album) key
    albums: Dict[Tuple[str, str], List[Tuple[Path, Dict]]] = {}
    for f, tags in all_items:
        # Use the determined key for this file's directory, or fall back to tags
        dir_path = f.parent
        if dir_path in dir_to_key:
            key = dir_to_key[dir_path]
        else:
            key = (normalize_album_artist(tags.get("artist", "") or ""), normalize_album_name(tags.get("album", "") or ""))
        
        albums.setdefault(key, []).append((f, tags))
    
    return albums


def choose_album_year(items: List[Tuple[Path, Dict[str, Any]]]) -> str:
    """
    Given a list of (path, tags) for an album, pick a canonical year
    to use in the folder name.

    Strategy:
      - Collect all non-empty year strings from tags["year"].
      - If none, return "" (no year in folder).
      - Otherwise:
          * Find the most common year.
          * If there's a tie, pick the earliest year numerically.
    """
    years = [t["year"] for (_p, t) in items if t.get("year")]
    if not years:
        return ""

    counts = Counter(years)
    max_count = max(counts.values())
    candidates = [y for y, c in counts.items() if c == max_count]

    numeric_candidates = []
    for y in candidates:
        try:
            numeric_candidates.append((int(y[:4]), y))
        except ValueError:
            numeric_candidates.append((9999, y))

    numeric_candidates.sort(key=lambda x: (x[0], x[1]))
    return numeric_candidates[0][1]


def sanitize_filename_component(name: str) -> str:
    """
    Make a string safe for use as a Windows/macOS filename component:
    - Replace invalid characters: <>:"/\\|?*
    - Strip trailing spaces and periods (Windows hates those)
    """
    invalid = '<>:"/\\|?*'
    sanitized = "".join("_" if c in invalid else c for c in name)
    # Windows: no trailing space or dot
    sanitized = sanitized.rstrip(" .")
    return sanitized


def format_track_filename(tags: Dict[str, Any], ext: str) -> str:
    """Format a track filename from tags."""
    safe_title = sanitize_filename_component(tags["title"])
    return f"{tags['tracknum']:02d} - {safe_title}{ext.lower()}"


def write_tags_to_file(path: Path, tags: Dict[str, Any], dry_run: bool = False, backup_enabled: bool = True, album_artist: Optional[str] = None) -> bool:
    """
    Write tags to an audio file.
    If backup_enabled is True, backs up the file first.
    Detects actual file format (not just extension) to handle misnamed files.

    When NORMALIZE_ARTIST_IN_TAGS is True, only albumartist is set to the normalized form
    (folder/grouping); artist (track artist) is left unchanged so compilations keep
    per-track artists and streamers can use albumartist for grouping.
    Pass album_artist when you know the album-level artist (e.g. "Various Artists"
    for compilations) so albumartist is set correctly; otherwise it is derived from
    tags["artist"].
    When NORMALIZE_ALBUM_IN_TAGS is True, the album tag is set to the normalized form
    (strip " (Disc 1)", " [Disc 2]", etc.) so streamers show one multi-disc album.
    """
    try:
        ext = path.suffix.lower()
        
        # Backup audio files before writing tags (if backup enabled)
        if backup_enabled and not dry_run:
            from artwork import backup_audio_file_if_needed
            backup_audio_file_if_needed(path, dry_run, backup_enabled)
        
        # Try to detect actual file format first (handles misnamed files)
        detected_format = None
        try:
            audio_test = MutagenFile(str(path))
            if audio_test is not None:
                # Detect format from MutagenFile type
                if hasattr(audio_test, 'mime'):
                    mime = audio_test.mime
                    if 'flac' in mime.lower():
                        detected_format = 'flac'
                    elif 'mp3' in mime.lower() or 'mpeg' in mime.lower():
                        detected_format = 'mp3'
                    elif 'mp4' in mime.lower() or 'm4a' in mime.lower():
                        detected_format = 'mp4'
                # Also check by class name
                class_name = type(audio_test).__name__.lower()
                if 'flac' in class_name:
                    detected_format = 'flac'
                elif 'mp3' in class_name or 'id3' in class_name:
                    detected_format = 'mp3'
                elif 'mp4' in class_name or 'm4a' in class_name:
                    detected_format = 'mp4'
        except Exception:
            pass  # Will try format-specific handlers below
        
        # Use detected format if available, otherwise fall back to extension
        use_format = detected_format or ext.lstrip('.')
        
        # Album artist for grouping: normalized, never overwrite track artist
        if NORMALIZE_ARTIST_IN_TAGS:
            effective_album_artist = normalize_album_artist((album_artist or tags.get("artist") or "").strip() or "")
        else:
            effective_album_artist = None
        
        # Album name: normalized so streamers index one multi-disc album (e.g. "Instrumental Magic" not "Instrumental Magic (Disc 1)")
        if NORMALIZE_ALBUM_IN_TAGS:
            tags = {**tags, "album": normalize_album_name(tags.get("album", "") or "")}
        
        # Try FLAC first (if detected or extension suggests it)
        if use_format == 'flac' or ext == ".flac":
            try:
                audio = FLAC(str(path))
                audio["TITLE"] = tags["title"]
                audio["ARTIST"] = tags["artist"]
                audio["ALBUM"] = tags["album"]
                if effective_album_artist is not None:
                    audio["ALBUMARTIST"] = effective_album_artist
                if tags.get("year"):
                    audio["DATE"] = tags["year"]
                audio["TRACKNUMBER"] = str(tags["tracknum"])
                if tags.get("discnum", 1) > 1:
                    audio["DISCNUMBER"] = str(tags["discnum"])
                if not dry_run:
                    audio.save()
                return True
            except Exception as e:
                if ext == ".flac":
                    # If extension says FLAC but it's not, try other formats
                    # Warning already logged via logmsg.warn() if available
                    pass
                else:
                    raise  # Re-raise if we weren't expecting FLAC
        
        # Try MP4/M4A (if detected or extension suggests it)
        if use_format in {'mp4', 'm4a'} or ext in {".mp4", ".m4a", ".m4v"}:
            try:
                audio = MP4(str(path))
                audio["\xa9nam"] = tags["title"]
                audio["\xa9ART"] = tags["artist"]
                audio["\xa9alb"] = tags["album"]
                if effective_album_artist is not None:
                    audio["aART"] = [effective_album_artist]
                if tags.get("year"):
                    audio["\xa9day"] = tags["year"]
                audio["trkn"] = [(tags["tracknum"], 0)]
                if tags.get("discnum", 1) > 1:
                    audio["disk"] = [(tags["discnum"], 0)]
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                if ext in {".mp4", ".m4a", ".m4v"}:
                    pass  # Try MP3 next
                else:
                    raise
        
        # Try MP3 (if detected or extension suggests it)
        if use_format == 'mp3' or ext == ".mp3":
            try:
                audio = EasyID3(str(path))
                audio["title"] = tags["title"]
                audio["artist"] = tags["artist"]
                audio["album"] = tags["album"]
                if effective_album_artist is not None:
                    audio["albumartist"] = effective_album_artist
                if tags.get("year"):
                    audio["date"] = tags["year"]
                audio["tracknumber"] = str(tags["tracknum"])
                if tags.get("discnum", 1) > 1:
                    audio["discnumber"] = str(tags["discnum"])
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                if ext == ".mp3":
                    pass  # Try generic next
                else:
                    raise
        
        # Try generic MutagenFile for other formats
        try:
            audio = MutagenFile(str(path), easy=True)
            if audio is not None and audio.tags:
                audio["title"] = tags["title"]
                audio["artist"] = tags["artist"]
                audio["album"] = tags["album"]
                if tags.get("year"):
                    audio["date"] = tags["year"]
                audio["tracknumber"] = str(tags["tracknum"])
                if tags.get("discnum", 1) > 1:
                    audio["discnumber"] = str(tags["discnum"])
                if not dry_run:
                    audio.save()
                return True
        except Exception:
            pass
                
        return False
        
    except Exception as e:
        return False

