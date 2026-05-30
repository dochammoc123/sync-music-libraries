"""
Tag operations for reading and processing audio file metadata.
"""
import os
import re
import unicodedata
from collections import Counter, defaultdict
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
        
        # Try to extract disc + track from filename like "2-01 " (common for multi-disc rips)
        discnum = 1
        disc_track_match = re.match(r'^\s*(\d{1,2})\s*[-_]\s*(\d{1,2})\b', title)
        if disc_track_match:
            try:
                discnum = int(disc_track_match.group(1))
            except ValueError:
                discnum = 1
            try:
                tracknum = int(disc_track_match.group(2))
            except ValueError:
                tracknum = 0
            # Remove the disc-track prefix (keep any remaining separator / title text)
            title = re.sub(r'^\s*\d{1,2}\s*[-_]\s*\d{1,2}\s*', '', title).strip()
        else:
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
            "discnum": discnum,
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


def _mutagen_tag_values(tags: Any, key: str) -> List[Any]:
    """Raw values for a tag key (EasyID3 list-of-str or ASF list-of-attribute objects)."""
    if tags is None:
        return []
    try:
        v = tags.get(key)
    except (KeyError, AttributeError):
        return []
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _mutagen_first_string(tags: Any, *keys: str, default: str = "") -> str:
    for key in keys:
        for item in _mutagen_tag_values(tags, key):
            raw = getattr(item, "value", item)
            if raw is None:
                continue
            s = str(raw).strip()
            if s:
                return s
    return default


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
        tags = audio.tags
        is_asf = path.suffix.lower() == ".wma" or type(audio).__name__ == "ASF"

        if is_asf:
            # WMA/ASF: Mutagen does not support easy=True mapping; tags use WM/* and Author/Title.
            albumartist = _mutagen_first_string(tags, "WM/AlbumArtist", "albumartist", "ALBUMARTIST")
            performer = _mutagen_first_string(tags, "Author", "artist", "ARTIST")
            # Track-level artist should come from performer/Author; albumartist may be a placeholder
            # like "Various" or even "Unknown\\Various Artists" and should not override the performer.
            artist = performer or "Unknown Artist"
            album = _mutagen_first_string(tags, "WM/AlbumTitle", "album", "ALBUM") or "Unknown Album"
            title = _mutagen_first_string(tags, "Title", "title") or path.stem
            date = _mutagen_first_string(tags, "WM/Year", "WM/ReleaseDate", "date", "year")
            trackno = _mutagen_first_string(tags, "WM/TrackNumber", "tracknumber", "TRACKNUMBER") or "0"
            discno_raw = _mutagen_first_string(
                tags, "WM/PartOfSet", "discnumber", "DISCNUMBER"
            )
            raw_albumartist = albumartist
        else:
            def _get(tag: str, default: str = "") -> str:
                v = tags.get(tag)
                return v[0] if v else default

            artist = _get("albumartist") or _get("artist") or "Unknown Artist"
            album = _get("album") or "Unknown Album"
            title = _get("title") or path.stem
            date = _get("date") or _get("year") or ""
            trackno = _get("tracknumber") or _get("TRACKNUMBER") or "0"
            discno_raw = _get("discnumber") or _get("DISCNUMBER") or ""
            raw_albumartist = (_get("albumartist") or _get("ALBUMARTIST") or "").strip()

        year = date[:4] if len(date) >= 4 and date[:4].isdigit() else ""

        try:
            tracknum = int(trackno.split("/")[0])
        except ValueError:
            tracknum = 0

        try:
            discnum = int((discno_raw or "1").split("/")[0])
        except ValueError:
            discnum = 1
        # Total discs when tag is "1/2" or "2/2" (for folder layout: use CD1 even when only disc 1 present)
        try:
            disctotal = int(discno_raw.split("/")[1]) if "/" in discno_raw else 0
        except (ValueError, IndexError):
            disctotal = 0

        # Heuristic: many multi-disc MP3 rips are tagged DISCNUMBER=1/1 for every file,
        # but filenames are prefixed like "2-01 Track Name". When that pattern matches
        # and the parsed track number agrees with the tag track number, treat it as disc N.
        # This is intentionally conservative to avoid "fixing" normal single-disc albums.
        if discnum == 1 and (not disctotal or disctotal <= 1):
            m = re.match(r'^\s*(\d{1,2})\s*[-_]\s*(\d{1,2})\b', path.stem)
            if m:
                try:
                    disc_from_name = int(m.group(1))
                except ValueError:
                    disc_from_name = 1
                try:
                    track_from_name = int(m.group(2))
                except ValueError:
                    track_from_name = 0
                if disc_from_name > 1 and track_from_name > 0 and tracknum == track_from_name:
                    discnum = disc_from_name

        return {
            "artist": artist.strip(),
            "album": album.strip(),
            "year": year.strip(),
            "tracknum": tracknum,
            "discnum": discnum,
            "disctotal": disctotal if disctotal > 0 else None,
            "discnumber_raw": (discno_raw or "").strip(),
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

UNKNOWN_ARTIST_TOKENS = {
    "",
    "unknown",
    "unkown",  # common typo
    "unknown artist",
    "unknown album artist",
    "various",
    "va",
    "n/a",
    "na",
}


def is_unknown_or_bucket_artist(s: str) -> bool:
    v = (s or "").strip().lower()
    if v in UNKNOWN_ARTIST_TOKENS:
        return True
    if v in GENERIC_COMPILATION_ARTISTS:
        return True
    # Mp3tag-style placeholders: "Unknown\Various Artists", "Unknown/Various Artists"
    if "unknown" in v and re.search(r"\bvarious\b|\bva\b", v):
        return True
    return False


def albumartist_needs_fixup(file_albumartist: str, canonical_albumartist: str) -> bool:
    """
    True when on-disk albumartist should be rewritten to ``canonical_albumartist``
    (Step 1.5), e.g. missing, placeholder, or mismatched after normalization.
    """
    aa = (file_albumartist or "").strip()
    canon = (canonical_albumartist or "").strip()
    if not canon:
        return False
    if not aa:
        return True
    if is_unknown_or_bucket_artist(aa):
        return True
    return (
        normalize_album_artist(aa).casefold()
        != normalize_album_artist(canon).casefold()
    )


def normalize_album_artist(artist: str) -> str:
    """
    Normalize album-level artist names for folder/label purposes.
    - Collapse generic bucket names (e.g. "Christmas Music") into "Various Artists"
      so that compilations don't end up under arbitrary pseudo-artist folders.
    - Normalize accents (e.g. "Céline Dion" -> "Celine Dion") so variants
      are grouped under one folder.
    """
    a = (artist or "").strip()
    if is_unknown_or_bucket_artist(a):
        return "Various Artists" if a.strip() else ""
    if a.lower() in GENERIC_COMPILATION_ARTISTS:
        return "Various Artists"
    return normalize_unicode_canonical(a)


def is_various_artists_compilation_folder_artist(artist: str) -> bool:
    """True when album-level artist maps to the Various Artists bucket (compilations, VA, generic buckets)."""
    a = (artist or "").strip()
    if not a:
        return False
    return normalize_album_artist(a) == "Various Artists"


def warn_if_compilation_needs_manual_tracklist_check() -> None:
    """
    Log a summary-visible reminder for compilations: many similar MusicBrainz releases exist;
    confirm track titles/edition manually when unsure.
    Call after begin_album and retarget_current_album_to_folder so the warning attaches to the right album.
    Emits at most once per album label per run (see run_state.consume_manual_tracklist_warning_once).
    """
    try:
        from structured_logging import logmsg
    except ImportError:
        return
    info = logmsg.current_album_info
    if not info:
        return
    artist, _, _ = info
    if not is_various_artists_compilation_folder_artist(artist):
        return
    label = logmsg.current_album_label
    if not label:
        return
    try:
        from run_state import consume_manual_tracklist_warning_once
    except ImportError:
        return
    if not consume_manual_tracklist_warning_once(label):
        return
    logmsg.warn(
        "Various-artist / compilation album: confirm track titles and edition manually when unsure "
        "(MusicBrainz often lists many similar releases).",
        count=False,
    )


# For two-artist albums: only file under the majority artist if they have at least this share
# of tracks (e.g. 2/3). Otherwise treat as Various Artists (e.g. 50/50 or 5/3).
MAJORITY_ARTIST_MIN_RATIO = 2 / 3

# Roman numerals I..X for "Vol. II" etc.
_ROMAN_TO_INT = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}


def _volume_num_to_int(match_group: str) -> Optional[int]:
    """Convert '2', 'II', 'III' etc. to int 1-10 for Volume/Vol. parsing."""
    if not match_group:
        return None
    s = match_group.strip()
    if s.isdigit():
        n = int(s)
        return n if 1 <= n <= 99 else None
    return _ROMAN_TO_INT.get(s.lower())


# Trailing "Volume N" / "Vol. N" only (not "[Disc N]" — those use different folder rules).
# Order: comma+Vol, then dash/hyphen+Vol ("Music- Vol.2"), then whitespace+Vol, then Volume.
_VOLUME_TAIL_PATTERNS: List[Tuple[re.Pattern[str], bool]] = [
    (re.compile(r"^(.+?)\s*,\s*Vol\.?\s*(\d+|[IVX]+)\s*$", re.IGNORECASE), True),  # strip comma from base
    (re.compile(r"^(.+?)\s*,\s*Volume\s+(\d+|[IVX]+)\s*$", re.IGNORECASE), True),
    (re.compile(r"^(.+?)[\s–-]+Vol\.?\s*(\d+|[IVX]+)\s*$", re.IGNORECASE), False),  # "…Music- Vol.2 [Live]"
    (re.compile(r"^(.+?)[\s–-]+Volume\s+(\d+|[IVX]+)\s*$", re.IGNORECASE), False),
    (re.compile(r"^(.+?)\s+Vol\.?\s*(\d+|[IVX]+)\s*$", re.IGNORECASE), False),
    (re.compile(r"^(.+?)\s+Volume\s+(\d+|[IVX]+)\s*$", re.IGNORECASE), False),
]

# Mid-string ``Vol. N: subtitle`` (common on box sets / series volumes).
_EMBEDDED_VOLUME_COLON_PATTERNS: List[Tuple[re.Pattern[str], bool]] = [
    (re.compile(r"^(.+?)\s*,\s*Vol\.?\s*(\d+|[IVX]+)\s*:\s*(.*)$", re.IGNORECASE), True),
    (re.compile(r"^(.+?)\s*,\s*Volume\s+(\d+|[IVX]+)\s*:\s*(.*)$", re.IGNORECASE), True),
    (re.compile(r"^(.+?)\s+Vol\.?\s*(\d+|[IVX]+)\s*:\s*(.*)$", re.IGNORECASE), False),
    (re.compile(r"^(.+?)\s+Volume\s+(\d+|[IVX]+)\s*:\s*(.*)$", re.IGNORECASE), False),
]


def _album_base_after_embedded_volume(base: str, subtitle: str, comma_before_vol: bool) -> str:
    """Series folder title after removing ``Vol. N:`` (keep subtitle when present)."""
    b = base.strip().rstrip(",").strip()
    sub = (subtitle or "").strip()
    if not sub:
        return b
    if comma_before_vol or b.endswith(","):
        return f"{b}, {sub}" if b else sub
    return f"{b} {sub}".strip()


def parse_trailing_volume_num(album: str) -> Optional[int]:
    """
    Return N if the album title ends with Volume N / Vol. N / , Vol. N (not [Disc N]).
    Prefer ``parse_album_layout_from_title`` for folder layout (VOL{n} vs CD{n}).
    """
    base, n = parse_trailing_volume_base_and_num(album)
    return n


def _strip_one_trailing_benign_parenthetical(s: str) -> Optional[str]:
    """
    If ``s`` ends with ``( … )`` where the inner text is edition fluff (not disc/volume
    metadata), return ``s`` without that suffix. Otherwise return None.

    Lets titles like ``…, Vol. 1 (Deluxe Version)`` match trailing ``Vol.`` patterns that
    require end-of-string. Does not strip ``(Disc 1)``, ``(Vol. 2)``, etc.
    """
    m = re.search(r"\s*\(([^)]*)\)\s*$", s)
    if not m:
        return None
    inner = m.group(1).strip()
    if re.search(r"\bdisc\s*\d", inner, re.IGNORECASE):
        return None
    if re.search(r"\bcd\s*\d", inner, re.IGNORECASE):
        return None
    if re.search(r"\bvol\.?\s*\d", inner, re.IGNORECASE):
        return None
    if re.search(r"\bvolume\s+\d", inner, re.IGNORECASE):
        return None
    return s[: m.start()].rstrip()


def _strip_trailing_benign_parentheticals(s: str) -> str:
    """Remove stacked trailing ``(Edition…)`` segments; see `_strip_one_trailing_benign_parenthetical`."""
    out = s.strip()
    for _ in range(8):
        nxt = _strip_one_trailing_benign_parenthetical(out)
        if not nxt or nxt == out:
            break
        out = nxt
    return out


def parse_trailing_volume_base_and_num(album: str) -> Tuple[Optional[str], Optional[int]]:
    """
    If title has embedded or trailing Volume/Vol., return (base_album, n); else (None, None).

    Embedded: ``Album, Vol. 1: Volume subtitle`` (colon required). Trailing: ``, Vol. 1`` etc.
    Trailing edition markers such as ``(Deluxe Version)`` after ``, Vol. 1`` are stripped
    first (when they are not disc/volume metadata) so the volume tail patterns can match.
    """
    if not album or not isinstance(album, str):
        return (None, None)
    s0 = album.strip()
    # Try full string, then without trailing [Live] / (Live) so "…Vol.2 [Live]" matches
    variants = [s0]
    s_live = re.sub(r"\s*[(\[]\s*Live\s*[)\]]\s*$", "", s0, flags=re.IGNORECASE).strip()
    if s_live and s_live != s0:
        variants.append(s_live)
    s_no_paren = _strip_trailing_benign_parentheticals(s0)
    if s_no_paren and s_no_paren not in variants:
        variants.append(s_no_paren)
    s_live_noparen = _strip_trailing_benign_parentheticals(s_live) if s_live else ""
    if s_live_noparen and s_live_noparen not in variants:
        variants.append(s_live_noparen)
    for s in variants:
        for pat, comma_base in _EMBEDDED_VOLUME_COLON_PATTERNS:
            m = pat.match(s)
            if not m:
                continue
            base = _album_base_after_embedded_volume(m.group(1), m.group(3), comma_base)
            n = _volume_num_to_int(m.group(2))
            if n is not None:
                return (base, n)
        for pat, comma_base in _VOLUME_TAIL_PATTERNS:
            m = pat.match(s)
            if not m:
                continue
            base = m.group(1).strip()
            if comma_base:
                base = base.rstrip(",").strip()
            n = _volume_num_to_int(m.group(2))
            if n is not None:
                return (base, n)
    return (None, None)


def parse_album_layout_from_title(
    album: str,
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Parse folder-layout hints from the album *title* (before relying only on tags).

    Returns (volume_n, disc_n_from_brackets, disc_title_after_brackets).
    - volume_n: trailing/embedded Vol./Volume on the part before [Disc N] (or whole title).
    - disc_n_from_brackets: N from ``[Disc N]`` / ``(Disc N)`` when present.
    - disc_title_after_brackets: text after ``[Disc N]`` if any (e.g. subtitle).

    Used with DISCNUMBER/DISCTOTAL to choose:
    ``CD{n}``, ``VOL{n}``, or ``VOL{n}/CD{m}`` under one library album folder.
    """
    if not album or not isinstance(album, str):
        return (None, None, None)
    s = album.strip()
    m = re.match(
        r"^(.*?)\s*[(\[]\s*disc\s*(\d+)\s*[)\]]\s*(.*)$", s, re.IGNORECASE
    )
    if m:
        base = m.group(1).strip()
        disc_n = int(m.group(2))
        dt = m.group(3).strip() or None
        _b, vol_n = parse_trailing_volume_base_and_num(base)
        return (vol_n, disc_n, dt)
    disc_n: Optional[int] = None
    s_for_vol = s
    m_disc = re.match(r"^(.*?)\s+disc\s+(\d+)\s*$", s, re.IGNORECASE)
    if m_disc:
        s_for_vol = m_disc.group(1).strip()
        disc_n = int(m_disc.group(2))
    else:
        m_cd = re.match(r"^(.*?)\s+cd\s+(\d+)\s*$", s, re.IGNORECASE)
        if m_cd:
            s_for_vol = m_cd.group(1).strip()
            disc_n = int(m_cd.group(2))
    _b, vol_n = parse_trailing_volume_base_and_num(s_for_vol)
    return (vol_n, disc_n, None)


_VOL_DIR_RE = re.compile(r"^VOL\d+", re.IGNORECASE)
# CD / CD1 / DVD / DVD1 … under an album folder (layout leaves). Plain "CD" / "DVD" included.
_MEDIA_LEAF_DIR_RE = re.compile(r"^(?:CD\d*|DVD\d*)$", re.IGNORECASE)
# Kept for call sites that only need the historical CD1-style name; prefer _MEDIA_LEAF_DIR_RE.
_CD_DIR_RE = _MEDIA_LEAF_DIR_RE


def is_library_path_medium_tail_part(name: str) -> bool:
    """
    True if ``name`` is a final path segment under Artist/Album/ that should collapse to the album
    root (e.g. ``CD``, ``CD1``, ``DVD``, ``VOL2``).
    """
    if not name:
        return False
    if _VOL_DIR_RE.match(name):
        return True
    return bool(_MEDIA_LEAF_DIR_RE.match(name))


def _natural_sort_key(name: str) -> List:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def album_layout_leaf_directories(album_dir: Path) -> List[Path]:
    """
    Leaf storage directories under an album folder in display order: each top-level
    ``CD`` / ``CD1`` / ``DVD`` / ``DVD1`` dir, or each ``VOL*/CD*`` (or same media names)
    nested dir, or a bare ``VOL*`` dir when it has no media children (one medium per volume).

    Used for web art (medium count, per-folder covers) and similar.
    """
    if not album_dir.is_dir():
        return []
    out: List[Path] = []
    try:
        subs = sorted(
            [p for p in album_dir.iterdir() if p.is_dir()],
            key=lambda p: _natural_sort_key(p.name),
        )
    except OSError:
        return []
    for sub in subs:
        if _CD_DIR_RE.match(sub.name):
            out.append(sub)
            continue
        if _VOL_DIR_RE.match(sub.name):
            try:
                inner = sorted(
                    [p for p in sub.iterdir() if p.is_dir()],
                    key=lambda p: _natural_sort_key(p.name),
                )
            except OSError:
                inner = []
            nested_media = [p for p in inner if _MEDIA_LEAF_DIR_RE.match(p.name)]
            if nested_media:
                out.extend(nested_media)
            else:
                out.append(sub)
    return out


def parse_album_disc(album: str) -> Tuple[str, Optional[int], Optional[str]]:
    """
    Parse disc number and optional disc title from album name for multi-disc sets
    with different titles per disc (e.g. "Solo (Disc 1) Mr Bad Guy" / "Solo (Disc 2) Barcelona").
    Also handles "Volume N" / "Vol. N" at end: "Eagles Greatest Hits Volume 2" -> (base, 2, None).
    Returns (base_album, disc_num, disc_title). disc_title is None when not present.
    """
    if not album or not isinstance(album, str):
        return (album or "", None, None)
    s = album.strip()
    # Match: "BaseAlbum (Disc N) Optional Title" or "BaseAlbum [Disc N] Optional Title"
    m = re.match(r"^(.*?)\s*[(\[]\s*disc\s*(\d+)\s*[)\]]\s*(.*)$", s, re.IGNORECASE)
    if m:
        base = m.group(1).strip()
        disc_num = int(m.group(2))
        rest = m.group(3).strip()
        disc_title = rest if rest else None
        return (normalize_album_name(base), disc_num, disc_title)
    # Trailing "Disc N" / "CD N" without brackets (common in older rips)
    m = re.match(r"^(.*?)\s+disc\s+(\d+)\s*$", s, re.IGNORECASE)
    if m:
        return (normalize_album_name(m.group(1).strip()), int(m.group(2)), None)
    m = re.match(r"^(.*?)\s+cd\s+(\d+)\s*$", s, re.IGNORECASE)
    if m:
        return (normalize_album_name(m.group(1).strip()), int(m.group(2)), None)
    base_vol, n_vol = parse_trailing_volume_base_and_num(s)
    if n_vol is not None and base_vol is not None:
        return (normalize_album_name(base_vol), n_vol, None)
    return (normalize_album_name(s), None, None)


def normalize_album_name(album: str) -> str:
    """
    Normalize album name for grouping and folder naming so multi-disc sets with
    inconsistent tags merge into one album (e.g. one folder with CD1/CD2 subdirs).
    Strips disc patterns from anywhere in the name: "(Disc 1)", "[Disc 2]", "Disc 1", "CD 2",
    "Volume 2", "Vol. 1", "(CD 1)", "[CD2]", " (1/2)", " - Disc 1", etc.
    Normalizes colon variants so "Greenpeace: Rainbow Warriors" and "Greenpeace Rainbow Warriors" match.
    Strips trailing comma so ", Vol. 1" is removed cleanly.
    """
    if not album or not isinstance(album, str):
        return album or ""
    s = album.strip()
    # Edition suffixes like "(Deluxe Version)" after ", Vol. 1" — strip first so volume/disc
    # patterns and grouping match the base release title.
    s = _strip_trailing_benign_parentheticals(s)
    # Strip disc patterns from anywhere (not just trailing)
    # [Disc N], (Disc N), [disc N], (disc N)
    s = re.sub(r'\s*[(\[]\s*disc\s*\d+\s*[)\]]\s*', ' ', s, flags=re.IGNORECASE)
    # [CD N], (CD N), [CDN], (CDN)
    s = re.sub(r'\s*[(\[]\s*cd\s*\d+\s*[)\]]\s*', ' ', s, flags=re.IGNORECASE)
    # (N/M), [N/M] e.g. (1/2), [2/2]
    s = re.sub(r'\s*[(\[]\s*\d+\s*/\s*\d+\s*[)\]]\s*', ' ', s)
    # - Disc N, – Disc N (dash + Disc N)
    s = re.sub(r'\s*[-–—]\s*disc\s*\d+\s*', ' ', s, flags=re.IGNORECASE)
    # Trailing Disc N / CD N without parens or dash (e.g. "... Love Songs Disc 1")
    s = re.sub(r'\s+disc\s+\d+\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+cd\s+\d+\s*$', '', s, flags=re.IGNORECASE)
    # Mid-string Vol. N: / Volume N: (drop volume marker; keep series + subtitle)
    s = re.sub(r'\s*,\s*Vol\.?\s*(\d+|[IVX]+)\s*:\s*', ', ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*,\s*Volume\s+(\d+|[IVX]+)\s*:\s*', ', ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+Vol\.?\s*(\d+|[IVX]+)\s*:\s*', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+Volume\s+(\d+|[IVX]+)\s*:\s*', ' ', s, flags=re.IGNORECASE)
    # [Live], (Live) - strip so "Album [Live]" and "Album" group
    s = re.sub(r'\s*[(\[]\s*Live\s*[)\]]\s*', ' ', s, flags=re.IGNORECASE)
    # Volume N, Vol. N: comma, dash, or space before (digits or roman I-X)
    s = re.sub(r'\s*,\s*Vol\.?\s*(\d+|[IVX]+)\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*,\s*Volume\s+(\d+|[IVX]+)\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[\s–-]+Vol\.?\s*(\d+|[IVX]+)\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[\s–-]+Volume\s+(\d+|[IVX]+)\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+Vol\.?\s*(\d+|[IVX]+)\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+Volume\s+(\d+|[IVX]+)\s*$', '', s, flags=re.IGNORECASE)
    # Trailing comma (leftover from ", Vol. 1" etc.)
    s = re.sub(r'\s*,\s*$', '', s)
    # Normalize colon so "Greenpeace: Rainbow Warriors" and "Greenpeace Rainbow Warriors" match
    s = re.sub(r'\s*:\s*', ' ', s)
    # Collapse multiple spaces and strip
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _majority_artist_from_track_strings(
    items: List[Tuple[Path, Dict[str, Any]]],
    min_ratio: float = MAJORITY_ARTIST_MIN_RATIO,
) -> Optional[str]:
    """
    When one performer name appears inside most track ``artist`` strings (including
    collaboration tags like ``Willie Nelson/Roger Miller/Faron Young``), return that
    artist for album folder grouping.
    """
    raw_artist_strings = [t["artist"] for (_p, t) in items if t.get("artist")]
    total = len(raw_artist_strings)
    if total == 0:
        return None
    candidates = sorted({normalize_album_artist(a) for a in raw_artist_strings if a})
    best: Optional[str] = None
    best_hits = 0
    threshold = max(3, int(total * min_ratio))
    for cand in candidates:
        cl = cand.lower()
        if not cl or cl == "various artists":
            continue
        hits = sum(1 for ra in raw_artist_strings if cl in (ra or "").lower())
        if hits > best_hits:
            best_hits = hits
            best = cand
    if best and best_hits >= threshold:
        return best
    return None


def _majority_meaningful_albumartist(
    items: List[Tuple[Path, Dict[str, Any]]],
    min_ratio: float = MAJORITY_ARTIST_MIN_RATIO,
) -> Optional[str]:
    """Most common non-placeholder ``albumartist`` when it dominates tagged files."""
    counts: Dict[str, int] = defaultdict(int)
    for _p, t in items:
        aa = (t.get("albumartist") or "").strip()
        if aa and not is_unknown_or_bucket_artist(aa):
            counts[normalize_album_artist(aa)] += 1
    if not counts:
        return None
    best, best_n = max(counts.items(), key=lambda x: x[1])
    tagged_aa = sum(counts.values())
    if best_n >= max(1, int(tagged_aa * min_ratio)):
        return best
    return None


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
    # Bucket by (normalized artist, casefolded album) so one download folder with mixed tags
    # ([Disc 1], Vol. 2, "Of" vs "of") still picks one album, not a false Various Artists tie.
    rows: List[Tuple[str, str]] = []
    for _p, t in items:
        if t.get("artist") and t.get("album"):
            # Prefer albumartist when it's present and meaningful; ignore placeholders like
            # "Unknown"/"Unkown"/"Various"/"VA" so we can infer from track artists.
            aa = (t.get("albumartist") or "").strip()
            ar = (t.get("artist") or "").strip()
            use_artist = aa if (aa and not is_unknown_or_bucket_artist(aa)) else ar
            rows.append((use_artist, normalize_album_name(t["album"])))
    
    if rows:
        bucket_count: Dict[Tuple[str, str], int] = defaultdict(int)
        bucket_rep: Dict[Tuple[str, str], str] = {}
        for raw_ar, alb_norm in rows:
            art_key = normalize_album_artist(raw_ar)
            bkey = (art_key, alb_norm.casefold())
            bucket_count[bkey] += 1
            if bkey not in bucket_rep or len(alb_norm) > len(bucket_rep[bkey]):
                bucket_rep[bkey] = alb_norm  # longest spelling wins (e.g. full title)
        
        max_count = max(bucket_count.values())
        top_keys = [k for k, c in bucket_count.items() if c == max_count]
        
        # Tie: two different albums equally common in same folder → compilation
        if len(top_keys) > 1:
            return ("Various Artists", bucket_rep[top_keys[0]])
        
        candidate_artist_norm, _album_cf = top_keys[0]
        candidate_album = bucket_rep[top_keys[0]]
        total_tracks = len(rows)
        majority_track = _majority_artist_from_track_strings(items)
        majority_aa = _majority_meaningful_albumartist(items)
        pair_below_majority = max_count < total_tracks * MAJORITY_ARTIST_MIN_RATIO

        distinct_artists = len(
            {normalize_album_artist(t["artist"]) for (_p, t) in items if t.get("artist")}
        )
        if distinct_artists >= 3:
            if majority_track:
                return (majority_track, candidate_album)
            return ("Various Artists", candidate_album)

        # Two track-level artist strings (e.g. "Roger Miller" + "A/Roger Miller/B") or a box
        # set with many source album titles: (artist, album) pair counts can be < 2/3 even
        # when one performer clearly owns the folder.
        if distinct_artists == 2 and pair_below_majority:
            pick = majority_aa or majority_track
            if pick:
                return (pick, candidate_album)
            return ("Various Artists", candidate_album)

        if pair_below_majority:
            pick = majority_aa or majority_track
            if pick:
                return (pick, candidate_album)

        return (candidate_artist_norm, candidate_album)
    
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
        # Use root album dir (same as step 1), not f.parent — files may live in CD1/ subfolders
        # under one logical album folder in Downloads.
        root_dir = find_root_album_directory(f, files, downloads_root)
        if root_dir in dir_to_key:
            key = dir_to_key[root_dir]
        else:
            key = (normalize_album_artist(tags.get("artist", "") or ""), normalize_album_name(tags.get("album", "") or ""))
        
        albums.setdefault(key, []).append((f, tags))

    # Step 4: Merge groups that share (artist, base_album) when album has disc-with-title
    # e.g. "Solo (Disc 1) Mr Bad Guy" and "Solo (Disc 2) Barcelona" -> one album "Solo" with CD1 - Mr Bad Guy / CD2 - Barcelona
    # Case-insensitive base merge: "…Of Women…" vs "…of Women…" share one folder.
    def _merged_album_key(
        merged_so_far: Dict[Tuple[str, str], List[Tuple[Path, Dict]]],
        artist: str,
        base_album: str,
    ) -> Tuple[str, str]:
        for (a, alb) in merged_so_far.keys():
            if a == artist and alb.casefold() == base_album.casefold():
                return (a, alb)
        return (artist, base_album)

    merged: Dict[Tuple[str, str], List[Tuple[Path, Dict]]] = {}
    for (artist, album), items in albums.items():
        # ``album`` is the directory-level winner from step 2/3 (e.g. box set with mixed
        # source album strings). Use it for grouping unless the tag has an explicit disc.
        canonical_album = album
        for (f, tags) in items:
            raw_album = (tags.get("album") or "").strip()
            base_album, disc_num, _ = parse_album_disc(raw_album)
            if disc_num is not None:
                base = base_album
            else:
                base = canonical_album
            key = _merged_album_key(merged, artist, base)
            merged.setdefault(key, []).append((f, tags))

    # Step 5: Merge keys when one album name is a prefix of another (same artist)
    # e.g. "The Solo Collection" and "The Solo Collection The Rarities 1" -> one album "The Solo Collection"
    def _canonical_album_key(artist: str, album: str, keys: List[Tuple[str, str]]) -> Tuple[str, str]:
        candidates = [(a, al) for (a, al) in keys if a == artist and (al == album or album.startswith(al + " "))]
        if not candidates:
            return (artist, album)
        return min(candidates, key=lambda x: len(x[1]))

    keys_list = list(merged.keys())
    final: Dict[Tuple[str, str], List[Tuple[Path, Dict]]] = {}
    for (artist, album), items in merged.items():
        ckey = _canonical_album_key(artist, album, keys_list)
        final.setdefault(ckey, []).extend(items)
    return final


def choose_album_year(items: List[Tuple[Path, Dict[str, Any]]]) -> str:
    """
    Given a list of (path, tags) for an album, pick a canonical year string
    for the folder name and label.

    Strategy:
      - Collect all non-empty year strings from tags["year"] (use first 4 digits).
      - If none, return "" (no year in folder or label).
      - Distinct years (sorted):
          * 1 year  -> "yyyy"
          * 2–3 years -> "yyyy, yyyy, yyyy"
          * 4+ years -> "yyyy - yyyy" (earliest to latest)
    """
    years = [t["year"] for (_p, t) in items if t.get("year")]
    if not years:
        return ""

    numeric_years: List[int] = []
    for y in years:
        s = (y or "").strip()
        if len(s) >= 4 and s[:4].isdigit():
            try:
                numeric_years.append(int(s[:4]))
            except ValueError:
                pass
    if not numeric_years:
        return ""

    distinct = sorted(set(numeric_years))
    if len(distinct) == 1:
        return str(distinct[0])
    if len(distinct) <= 3:
        return ", ".join(str(y) for y in distinct)
    return f"{distinct[0]} - {distinct[-1]}"


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
                elif 'asf' in class_name:
                    detected_format = 'asf'
        except Exception:
            pass  # Will try format-specific handlers below
        
        # Use detected format if available, otherwise fall back to extension
        use_format = detected_format or ext.lstrip('.')
        
        # Album artist for grouping: normalized, never overwrite track artist
        if NORMALIZE_ARTIST_IN_TAGS:
            raw_aa = (
                album_artist
                or tags.get("albumartist")
                or tags.get("artist")
                or ""
            ).strip()
            effective_album_artist = (
                normalize_album_artist(raw_aa) if raw_aa else None
            )
        else:
            effective_album_artist = None
        
        # Disc tag: do NOT rewrite disc tags unless we have to.
        #
        # Why: `add_missing_tags_global()` often calls `write_tags_to_file()` just to fill albumartist.
        # Those reads can carry odd/incorrect totals (e.g. "1/10") that we must not “spread” by rewriting.
        #
        # Policy:
        # - If caller provides an explicit `discnumber` string, write it (this is our “fix totals” path).
        # - Otherwise, only write a disc tag when discnum > 1 (so disc 2/3/etc still get tagged).
        # - Never write disc 1’s total purely because `disctotal > 1` was observed during a read.
        discnum = int(tags.get("discnum", 1) or 1)
        disctotal_raw = tags.get("disctotal")
        try:
            disctotal = int(disctotal_raw) if disctotal_raw is not None else 0
        except (TypeError, ValueError):
            disctotal = 0
        discnumber_present = "discnumber" in tags
        discnumber_raw_val = tags.get("discnumber")
        discnumber_str = (str(discnumber_raw_val).strip() if discnumber_raw_val is not None else "")
        discnumber_clear = discnumber_present and discnumber_str == ""
        discnumber_str = discnumber_str if discnumber_str else None

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
                if discnumber_clear:
                    if "DISCNUMBER" in audio:
                        del audio["DISCNUMBER"]
                elif discnumber_str or discnum > 1:
                    audio["DISCNUMBER"] = discnumber_str or (f"{discnum}/{disctotal}" if disctotal > 1 else str(discnum))
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
                if discnumber_clear:
                    if "disk" in audio:
                        del audio["disk"]
                elif discnumber_str or discnum > 1:
                    # MP4 uses a tuple (discnum, disctotal). Use 0 when total unknown.
                    audio["disk"] = [(discnum, disctotal if disctotal > 0 else 0)]
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
                if discnumber_clear:
                    if "discnumber" in audio:
                        del audio["discnumber"]
                elif discnumber_str or discnum > 1:
                    audio["discnumber"] = discnumber_str or (f"{discnum}/{disctotal}" if disctotal > 1 else str(discnum))
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                if ext == ".mp3":
                    pass  # Try generic next
                else:
                    raise

        # WMA/ASF
        if use_format == "asf" or ext == ".wma":
            try:
                from mutagen.asf import ASF

                audio = ASF(str(path))
                if audio.tags is None:
                    audio.add_tags()
                audio.tags["Title"] = tags["title"]
                audio.tags["Author"] = tags["artist"]
                audio.tags["WM/AlbumTitle"] = tags["album"]
                if effective_album_artist:
                    audio.tags["WM/AlbumArtist"] = effective_album_artist
                if tags.get("year"):
                    audio.tags["WM/Year"] = tags["year"]
                audio.tags["WM/TrackNumber"] = str(tags["tracknum"])
                if discnumber_str or discnum > 1:
                    audio.tags["WM/PartOfSet"] = discnumber_str or (
                        f"{discnum}/{disctotal}" if disctotal > 1 else str(discnum)
                    )
                elif discnumber_clear and "WM/PartOfSet" in audio.tags:
                    del audio.tags["WM/PartOfSet"]
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                if ext == ".wma":
                    pass
                else:
                    raise
        
        # Try generic MutagenFile for other formats
        try:
            audio = MutagenFile(str(path), easy=True)
            if audio is not None and audio.tags:
                audio["title"] = tags["title"]
                audio["artist"] = tags["artist"]
                audio["album"] = tags["album"]
                if effective_album_artist:
                    audio["albumartist"] = effective_album_artist
                if tags.get("year"):
                    audio["date"] = tags["year"]
                audio["tracknumber"] = str(tags["tracknum"])
                if discnumber_clear:
                    if "discnumber" in audio:
                        del audio["discnumber"]
                elif discnumber_str or discnum > 1:
                    audio["discnumber"] = discnumber_str or (f"{discnum}/{disctotal}" if disctotal > 1 else str(discnum))
                if not dry_run:
                    audio.save()
                return True
        except Exception:
            pass
                
        return False
        
    except Exception as e:
        return False


def update_discnumber_only(path: Path, discnumber: str, dry_run: bool = False, backup_enabled: bool = True) -> bool:
    """
    Update ONLY the disc tag for a file (do not rewrite title/artist/album/date/track).
    `discnumber` may be "", "1", "1/1", "2/2", etc. Empty string removes the tag.
    """
    try:
        if backup_enabled and not dry_run:
            from artwork import backup_audio_file_if_needed
            backup_audio_file_if_needed(path, dry_run, backup_enabled)

        discnumber = (discnumber or "").strip()
        ext = path.suffix.lower()
        detected_format = None
        try:
            audio_test = MutagenFile(str(path))
            if audio_test is not None:
                class_name = type(audio_test).__name__.lower()
                if "flac" in class_name:
                    detected_format = "flac"
                elif "mp4" in class_name or "m4a" in class_name:
                    detected_format = "mp4"
                elif "mp3" in class_name or "id3" in class_name:
                    detected_format = "mp3"
        except Exception:
            pass
        use_format = detected_format or ext.lstrip(".")

        # FLAC
        if use_format == "flac" or ext == ".flac":
            try:
                audio = FLAC(str(path))
                if discnumber:
                    audio["DISCNUMBER"] = discnumber
                else:
                    if "DISCNUMBER" in audio:
                        del audio["DISCNUMBER"]
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                pass

        # MP4/M4A
        if use_format in {"mp4", "m4a"} or ext in {".mp4", ".m4a", ".m4v"}:
            try:
                audio = MP4(str(path))
                if discnumber:
                    # Parse "n/total" or "n"
                    parts = discnumber.split("/", 1)
                    n = int(parts[0]) if parts[0].isdigit() else 1
                    total = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                    audio["disk"] = [(n, total)]
                else:
                    if "disk" in audio:
                        del audio["disk"]
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                pass

        # MP3
        if use_format == "mp3" or ext == ".mp3":
            try:
                audio = EasyID3(str(path))
                if discnumber:
                    audio["discnumber"] = discnumber
                else:
                    if "discnumber" in audio:
                        del audio["discnumber"]
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                pass

        # Generic
        try:
            audio = MutagenFile(str(path), easy=True)
            if audio is not None and audio.tags:
                if discnumber:
                    audio["discnumber"] = discnumber
                else:
                    if "discnumber" in audio:
                        del audio["discnumber"]
                if not dry_run:
                    audio.save()
                return True
        except Exception:
            pass
        return False
    except Exception:
        return False


def update_albumartist_only(path: Path, album_artist: str, dry_run: bool = False, backup_enabled: bool = True) -> bool:
    """
    Update ONLY albumartist for a file (do not rewrite title/artist/album/date/track/disc).
    """
    try:
        if backup_enabled and not dry_run:
            from artwork import backup_audio_file_if_needed
            backup_audio_file_if_needed(path, dry_run, backup_enabled)

        aa = (album_artist or "").strip()
        if NORMALIZE_ARTIST_IN_TAGS:
            aa = normalize_album_artist(aa)

        ext = path.suffix.lower()
        detected_format = None
        try:
            audio_test = MutagenFile(str(path))
            if audio_test is not None:
                class_name = type(audio_test).__name__.lower()
                if "flac" in class_name:
                    detected_format = "flac"
                elif "mp4" in class_name or "m4a" in class_name:
                    detected_format = "mp4"
                elif "mp3" in class_name or "id3" in class_name:
                    detected_format = "mp3"
                elif "asf" in class_name:
                    detected_format = "asf"
        except Exception:
            pass
        use_format = detected_format or ext.lstrip(".")

        if use_format == "asf" or ext == ".wma":
            try:
                from mutagen.asf import ASF

                audio = ASF(str(path))
                if audio.tags is None:
                    audio.add_tags()
                audio.tags["WM/AlbumArtist"] = aa
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                pass

        if use_format == "flac" or ext == ".flac":
            try:
                audio = FLAC(str(path))
                audio["ALBUMARTIST"] = aa
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                pass

        if use_format in {"mp4", "m4a"} or ext in {".mp4", ".m4a", ".m4v"}:
            try:
                audio = MP4(str(path))
                audio["aART"] = [aa]
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                pass

        if use_format == "mp3" or ext == ".mp3":
            try:
                audio = EasyID3(str(path))
                audio["albumartist"] = aa
                if not dry_run:
                    audio.save()
                return True
            except Exception:
                pass

        # Generic
        try:
            audio = MutagenFile(str(path), easy=True)
            if audio is not None and audio.tags:
                audio["albumartist"] = aa
                if not dry_run:
                    audio.save()
                return True
        except Exception:
            pass
        return False
    except Exception:
        return False

