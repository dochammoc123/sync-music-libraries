"""
Artwork handling: embedding, fetching, and managing album artwork.
"""
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import musicbrainzngs
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, ID3NoHeaderError, APIC
from mutagen.mp4 import MP4, MP4Cover
from mutagen import File as MutagenFile
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from config import (
    BACKUP_ROOT,
    ENABLE_WEB_ART_LOOKUP,
    MB_APP,
    MB_CONTACT,
    MB_VER,
    MUSIC_ROOT,
    WEB_ART_LOOKUP_RETRIES,
    WEB_ART_LOOKUP_TIMEOUT,
)
from logging_utils import album_label_from_dir

# Last multi-folder CAA attempt: (n_saved, n_wanted, attempted). Subfolder copyFromRoot
# should not run when attempted and 0 < n_saved < n_wanted (or n_saved==0) so we do
# not stamp the root/CD1 image onto every CDn.
_LAST_PER_DISC_CAA: Tuple[int, int, bool] = (0, 0, False)
_DISC_CAA_COMMENT_RE = re.compile(
    r"(?:Disc|CD)\s*(\d+)(?:\s*cover)?",
    re.IGNORECASE,
)
_WARNED_SUBSET_RELEASES: set = set()  # (album_dir_str, mbid, need_leaves, media_count)


def _reset_last_per_disc_caa() -> None:
    global _LAST_PER_DISC_CAA
    _LAST_PER_DISC_CAA = (0, 0, False)


def last_per_disc_caa_stats() -> Tuple[int, int, bool]:
    return _LAST_PER_DISC_CAA


def all_leaf_folders_bytes_match_root(
    album_dir: Path, leaves: List[Path], ref: Path
) -> bool:
    """
    True when every layout leaf has folder.jpg and its bytes match `ref` (e.g. album
    cover.jpg) — the usual "copied the same file into every CDn/" mistake.
    """
    if not ref.is_file() or not leaves:
        return False
    try:
        rb = ref.read_bytes()
    except OSError:
        return False
    for L in leaves:
        p = L / "folder.jpg"
        if not p.is_file():
            return False
        try:
            if p.read_bytes() != rb:
                return False
        except OSError:
            return False
    return True


def _caa_comment_disc_set_from_data(data: Dict[str, Any]) -> set:
    s: set = set()
    for img in data.get("images", []):
        c = (img.get("comment") or "").strip()
        m = _DISC_CAA_COMMENT_RE.search(c)
        if m:
            s.add(int(m.group(1)))
    return s


def init_musicbrainz() -> None:
    """Initialize MusicBrainz user agent."""
    musicbrainzngs.set_useragent(MB_APP, MB_VER, MB_CONTACT)


def export_embedded_art_to_cover(first_file: Path, cover_path: Path, dry_run: bool = False) -> bool:
    """
    Export embedded artwork from the first audio file to cover.jpg.
    Returns True if successful, False otherwise.
    Supports FLAC, MP3, and MP4/M4A formats.
    """
    mf = MutagenFile(str(first_file))
    if mf is None:
        return False

    # FLAC files
    if isinstance(mf, FLAC):
        if mf.pictures:
            if not dry_run:
                cover_path.write_bytes(mf.pictures[0].data)
            return True
        return False

    # MP4/M4A files
    if isinstance(mf, MP4):
        try:
            # MP4 files store artwork in the 'covr' atom
            if 'covr' in mf:
                # Get the first cover image
                cover = mf['covr'][0]
                if isinstance(cover, MP4Cover):
                    if not dry_run:
                        cover_path.write_bytes(cover)
                    return True
        except Exception as e:
            # Log but don't fail - try other methods
            pass

    # MP3 files (ID3/APIC)
    try:
        id3 = ID3(str(first_file))
        pics = [f for f in id3.values() if isinstance(f, APIC)]
        if pics:
            if not dry_run:
                cover_path.write_bytes(pics[0].data)
            return True
    except Exception:
        pass

    return False


def fetch_art_from_web(
    artist: str,
    album: str,
    cover_path: Path,
    dry_run: bool = False,
    album_dir: Optional[Path] = None,
    subfolders_only: bool = False,
) -> Tuple[bool, Optional[str]]:
    """
    Try MusicBrainz + Cover Art Archive with retry logic.
    When album_dir has CD subfolders: prefers a MusicBrainz release with matching disc count
    (or at least that many media when you own a subset of discs, e.g. 6/10),
    then fetches disc-specific covers from CAA (by comment "Disc N"/"CD N" or by front-image order;
    CAA often tags per-disc art as *Booklet* with a "Disc N cover" comment, not *Front*).
    If ``subfolders_only`` is True, does not write ``cover_path`` when it already exists
    (typical: root came from embedded art) but still fetches CAA and fills CD/VOL subfolders.
    Returns (True, None) on success, (False, reason) on failure.
    """
    from structured_logging import logmsg
    if not ENABLE_WEB_ART_LOOKUP:
        return (False, "web art lookup disabled")

    try:
        _reset_last_per_disc_caa()
        init_musicbrainz()
        # When we have CD subdirs, fetch more candidates and prefer a release with matching disc count
        num_discs_wanted: Optional[int] = None
        if album_dir and album_dir.exists():
            from tag_operations import album_layout_leaf_directories

            leaves = album_layout_leaf_directories(album_dir)
            if leaves:
                num_discs_wanted = len(leaves)

        # MusicBrainz search is sensitive to extra suffixes. Our album strings can be polluted by:
        # - trailing year lists "(1985, 1992, 2000)"
        # - disc titles ("(Disc 1) Mr. Bad Guy")
        # - accidental artist prefixes ("Freddie Mercury - The Solo Collection ...")
        search_album_raw = (album or "").strip()
        search_album = search_album_raw
        try:
            from tag_operations import normalize_album_name, parse_album_disc

            search_album = normalize_album_name(search_album)
            base_album, _dn, _dt = parse_album_disc(search_album)
            search_album = (base_album or search_album).strip()
        except Exception:
            pass
        search_album = re.sub(r"\s*\(\d{4}(?:\s*[,\-]\s*\d{4})*\)\s*$", "", search_album).strip()
        if artist:
            # Strip "Artist - " prefix if present
            ap = f"{artist.strip()} - "
            if search_album.lower().startswith(ap.lower()):
                search_album = search_album[len(ap):].strip()
        search_album = re.sub(r"\s{2,}", " ", search_album).strip()

        # If the *raw* album name contains a trailing Vol/Volume hint, preserve it for MB search
        # (normalize_album_name() intentionally strips it for grouping, but MB uses it to disambiguate
        # between a single volume vs a full box set).
        vol_base, vol_n = (None, None)
        try:
            from tag_operations import parse_trailing_volume_base_and_num as _parse_vol

            vol_base, vol_n = _parse_vol(search_album_raw)
        except Exception:
            vol_base, vol_n = (None, None)
        if vol_n is not None:
            # Normalize to "Vol. N" (short form tends to work well)
            search_album = f"{(vol_base or search_album).strip()} Vol. {vol_n}"

        limit = 25 if num_discs_wanted else 5
        logmsg.verbose(
            "MusicBrainz search: artist={artist} release={release} (raw={raw})",
            artist=artist,
            release=search_album,
            raw=search_album_raw,
        )
        result = musicbrainzngs.search_releases(
            artist=artist, release=search_album, limit=limit
        )
        releases = result.get("release-list", [])
        if not releases and search_album != search_album_raw:
            # Fallback: try the raw album string if normalization stripped too much
            raw_album = search_album_raw
            logmsg.verbose(
                "MusicBrainz search fallback: artist={artist} release={release}",
                artist=artist,
                release=raw_album,
            )
            result = musicbrainzngs.search_releases(
                artist=artist, release=raw_album, limit=limit
            )
            releases = result.get("release-list", [])
        if not releases:
            return (False, f"no MusicBrainz release (searched: {search_album!r})")

        # Safety filter: MusicBrainz text search can return wrong artists even when `artist=` is provided.
        # For non-compilation artists, prefer releases whose artist credit phrase contains the artist name.
        if artist and artist.strip() and artist.strip().lower() != "various artists":
            want = artist.strip().lower()
            filtered_by_artist = []
            for r in releases:
                acp = (r.get("artist-credit-phrase") or "").strip().lower()
                if not acp:
                    # Older MB search responses may omit this; keep for now.
                    filtered_by_artist.append(r)
                    continue
                if want in acp:
                    filtered_by_artist.append(r)
            if filtered_by_artist and len(filtered_by_artist) != len(releases):
                logmsg.verbose(
                    "MusicBrainz: filtered candidates by artist credit ({a}->{b})",
                    a=len(releases),
                    b=len(filtered_by_artist),
                )
                releases = filtered_by_artist

        # If we were searching for a specific volume, prefer MB releases whose title/disambiguation
        # also contains that volume token.
        if vol_n is not None:
            vol_re = re.compile(rf"\bVol\.?\s*{vol_n}\b|\bVolume\s*{vol_n}\b", re.IGNORECASE)
            filtered = []
            for r in releases:
                t = (r.get("title") or "") + " " + (r.get("disambiguation") or "")
                if vol_re.search(t):
                    filtered.append(r)
            if filtered:
                logmsg.verbose(
                    "MusicBrainz: filtered candidates by Vol {n} hint ({a}->{b})",
                    n=vol_n,
                    a=len(releases),
                    b=len(filtered),
                )
                releases = filtered

        # Build CD subdir map early so we can prefer a release with disc-specific CAA art.
        # IMPORTANT: Only treat leaf folders as "discs" when they are actually CD* leaves.
        # Volume leaves (VOL1/VOL2/…) are separate releases/parts in many libraries and should NOT
        # be mapped to "Disc 1/Disc 2" CAA art, otherwise we end up writing the wrong cover into VOL2.
        cd_subdirs: Dict[int, Path] = {}
        if album_dir and album_dir.exists():
            from tag_operations import album_layout_leaf_directories

            # Prefer disc index parsed from folder name (e.g. "CD1 - Mr. Bad Guy"),
            # fallback to sequential index when no number is present.
            cdnum_re = re.compile(r"\bCD\s*(\d+)\b", re.IGNORECASE)
            used: set = set()
            fallback: List[Path] = []
            leaves = list(album_layout_leaf_directories(album_dir))
            saw_cd = any(cdnum_re.search(p.name) for p in leaves)
            for subdir in leaves:
                m = cdnum_re.search(subdir.name)
                if m:
                    try:
                        dn = int(m.group(1))
                    except ValueError:
                        dn = 0
                    if dn > 0 and dn not in used:
                        cd_subdirs[dn] = subdir
                        used.add(dn)
                        continue
                # Only assign sequential disc indices when we have at least one explicit CD leaf.
                if saw_cd:
                    fallback.append(subdir)
            if saw_cd:
                # Fill any gaps sequentially in display order
                next_i = 1
                for subdir in fallback:
                    while next_i in used:
                        next_i += 1
                    cd_subdirs[next_i] = subdir
                    used.add(next_i)
                    next_i += 1

        mbid = releases[0]["id"]
        selected_release_title: Optional[str] = None
        if num_discs_wanted and cd_subdirs and len(releases) > 0:
            # Prefer the release whose CAA has the best "Disc 1..N cover" comment coverage.
            # Do NOT require MB "medium-list" lookups here (they're slower and can rate-limit);
            # use CAA as the primary signal for per-disc art availability.
            best_score = -1
            best_idx = 9999
            best_mbid: Optional[str] = None
            for idx, r in enumerate(releases):
                rid = r.get("id")
                if not rid:
                    continue
                try:
                    crm = requests.get(
                        f"https://coverartarchive.org/release/{rid}",
                        timeout=WEB_ART_LOOKUP_TIMEOUT,
                    )
                    if crm.status_code != 200:
                        continue
                    cset = _caa_comment_disc_set_from_data(crm.json())
                except Exception:
                    continue
                score = sum(1 for d in range(1, num_discs_wanted + 1) if d in cset)
                logmsg.verbose(
                    "CAA candidate {rid}: disc-comment score={score}/{need}",
                    rid=rid,
                    score=score,
                    need=num_discs_wanted,
                )
                if score > best_score or (score == best_score and idx < best_idx):
                    best_score, best_idx, best_mbid = score, idx, rid
                if score == num_discs_wanted:
                    break

            if best_mbid and best_score > 0:
                mbid = best_mbid
            if num_discs_wanted and cd_subdirs:
                try:
                    rel = musicbrainzngs.get_release_by_id(mbid, includes=["media"])
                    nmed = len(rel.get("release", {}).get("medium-list", []))
                    selected_release_title = rel.get("release", {}).get("title")
                except Exception:
                    nmed = -1
                logmsg.verbose(
                    "CAA release selected: {mbid} (media={nmed}, need_leaves={need})",
                    mbid=mbid,
                    nmed=nmed,
                    need=num_discs_wanted,
                )
                # If we have fewer local discs than the selected release, we are applying a subset of
                # that release's per-disc art. This can be correct (you own part of a box set) or wrong
                # (different compilation where Disc 3 isn't the same). Warn once per album so it appears
                # in the summary and prompts a manual overlay when needed.
                # Only warn when the local layout is itself multi-disc (2+ leaves). For 1-disc layouts,
                # selecting a multi-disc release just to get a front cover isn't inherently suspicious.
                if album_dir and num_discs_wanted > 1 and nmed > num_discs_wanted:
                    key = (str(album_dir), mbid, int(num_discs_wanted), int(nmed))
                    if key not in _WARNED_SUBSET_RELEASES:
                        _WARNED_SUBSET_RELEASES.add(key)
                        logmsg.warn(
                            "Using disc art from a larger MusicBrainz release (media={nmed}) for this {need}-disc folder layout. If disc titles differ, use overlay/manual artwork.",
                            nmed=nmed,
                            need=num_discs_wanted,
                        )

        def _title_token_overlap(a: str, b: str) -> float:
            """
            Return a conservative 0..1 token overlap score between two strings.
            Used as a guardrail to avoid downloading clearly-wrong web art.
            """
            try:
                from tag_operations import normalize_album_name
                aa = normalize_album_name(a or "").lower()
                bb = normalize_album_name(b or "").lower()
            except Exception:
                aa = (a or "").lower()
                bb = (b or "").lower()
            ta = {t for t in re.split(r"[^a-z0-9]+", aa) if len(t) >= 3}
            tb = {t for t in re.split(r"[^a-z0-9]+", bb) if len(t) >= 3}
            if not ta or not tb:
                return 0.0
            return len(ta & tb) / float(min(len(ta), len(tb)))

        # Guardrail: MusicBrainz search can return a different compilation with a "valid" CAA cover.
        # If the selected MB release title barely overlaps our (normalized) album string, skip web art
        # rather than downloading/embedding an obviously wrong cover.
        try:
            if selected_release_title is None:
                for r in releases:
                    if r.get("id") == mbid:
                        selected_release_title = r.get("title")
                        break
            if selected_release_title:
                overlap = _title_token_overlap(search_album, selected_release_title)
                # Very permissive threshold: only block results that are clearly different.
                if overlap < 0.35:
                    logmsg.warn(
                        "Web art skipped: MusicBrainz release title mismatch (album={album!r} mb_title={mb_title!r} overlap={overlap:.2f}). Use overlay/manual artwork if needed.",
                        album=search_album,
                        mb_title=selected_release_title,
                        overlap=overlap,
                    )
                    return (False, f"MusicBrainz title mismatch (mbid={mbid})")
        except Exception:
            pass

        def _image_url(img: dict) -> Optional[str]:
            """Prefer 1200px, then full image, then 500px."""
            thumbs = img.get("thumbnails") or {}
            return thumbs.get("1200") or img.get("image") or thumbs.get("500")

        def _is_disc_specific(img: dict) -> bool:
            c = (img.get("comment") or "").strip()
            return bool(_DISC_CAA_COMMENT_RE.search(c))

        # When we have CD subfolders, fetch full CAA metadata to get disc-specific images
        if cd_subdirs:
            global _LAST_PER_DISC_CAA
            n_wanted_leaves = len(cd_subdirs)
            leaf_indices = sorted(cd_subdirs.keys())
            skip_per_disc_writes = (n_wanted_leaves == 1 and leaf_indices and leaf_indices[0] != 1)
            # Multi-disc: fetch metadata and get all disc covers
            meta_url = f"https://coverartarchive.org/release/{mbid}"
            rm = requests.get(meta_url, timeout=WEB_ART_LOOKUP_TIMEOUT)
            if rm.status_code != 200:
                _LAST_PER_DISC_CAA = (0, n_wanted_leaves, True)
                return (False, f"Cover Art Archive HTTP {rm.status_code}")
            data = rm.json()
            images = data.get("images", [])
            if not images:
                _LAST_PER_DISC_CAA = (0, n_wanted_leaves, True)
                return (False, "no images in Cover Art Archive")

            # Root cover selection:
            # - prefer explicit CAA `front: true` images that are NOT disc-specific
            # - else, use the first non-disc-specific image
            # This avoids picking "Disc 1 cover" as the album root when CAA marks it front.
            want_album_root = (not subfolders_only) or (not cover_path.exists())
            if want_album_root:
                front_candidates = [i for i in images if i.get("front") and not _is_disc_specific(i)]
                if front_candidates:
                    front_img = front_candidates[0]
                else:
                    non_disc = [i for i in images if not _is_disc_specific(i)]
                    front_img = non_disc[0] if non_disc else None

                # Multi-disc: if CAA has no non-disc-specific image at all, prefer leaving the
                # album root empty rather than writing a wrong disc cover as cover.jpg.
                if front_img is None:
                    logmsg.verbose(
                        "CAA: no non-disc 'front' image available; leaving album root cover.jpg empty"
                    )
                else:
                    img_url = _image_url(front_img)
                    if not img_url:
                        if not (subfolders_only and cover_path.exists()):
                            _LAST_PER_DISC_CAA = (0, n_wanted_leaves, True)
                            return (False, "no image URL in Cover Art Archive")
                    if img_url:
                        r = requests.get(img_url, timeout=WEB_ART_LOOKUP_TIMEOUT)
                        if r.status_code != 200:
                            if not (subfolders_only and cover_path.exists()):
                                _LAST_PER_DISC_CAA = (0, n_wanted_leaves, True)
                                return (False, f"failed to fetch front image: HTTP {r.status_code}")
                        else:
                            if not dry_run:
                                cover_path.write_bytes(r.content)
            else:
                logmsg.verbose(
                    "CAA: skip writing album root (cover already present); fetching per-disc art only"
                )

            # Heuristic: if disc folders are "generic" (CD1/CD2 only) with no disc title text,
            # it's often safer to keep the album front/box art on all discs rather than applying
            # disc-specific booklet scans from CAA (which may be inconsistent across releases).
            #
            # Named discs look like: "CD1 - Mr. Bad Guy", "CD2 - Barcelona", etc.
            disc_dir_has_titles = False
            for dn, p in cd_subdirs.items():
                # strip leading "CDn" and common separators
                remainder = re.sub(r"^\s*CD\s*\d+\s*[-–—_:]*\s*", "", p.name, flags=re.IGNORECASE).strip()
                if remainder and remainder.lower() != p.name.lower():
                    disc_dir_has_titles = True
                    break
            if n_wanted_leaves > 1 and not disc_dir_has_titles and subfolders_only:
                logmsg.warn(
                    "Disc folders are generic (CD1/CD2/…); using album front/box art for all discs instead of disc-specific CAA images. Overlay/manual artwork if you want per-disc art."
                )
                _LAST_PER_DISC_CAA = (0, 0, False)
                return (True, None)

            # If the local library only contains a later disc (e.g. only CD2 exists), per-disc art
            # from CAA is likely to be misleading and will then be embedded. Keep the root cover
            # (front/box) but skip writing disc-specific art. Allow later "copy root -> CD folder"
            # behavior to populate the lone disc folder with the front art.
            if skip_per_disc_writes:
                # Root pass: still download the album front/box cover above, but avoid writing any
                # disc-specific art. Warn only on the subfolders pass (that's where the user
                # expects disc art decisions).
                if subfolders_only and album_dir:
                    leaf_dir = cd_subdirs[leaf_indices[0]]
                    try:
                        leaf_rel = leaf_dir.relative_to(album_dir).as_posix()
                    except Exception:
                        leaf_rel = leaf_dir.name
                    logmsg.warn(
                        "Only {leaf} is present for this multi-disc release; skipping per-disc art download to avoid embedding the wrong disc cover. Disc folder will use the album front/box art unless you overlay manual artwork.",
                        leaf=leaf_rel,
                    )
                _LAST_PER_DISC_CAA = (0, 0, False)
                return (True, None)

            # Collect all front images in order (for order-based fallback)
            front_images = [i for i in images if i.get("front")]
            if not front_images:
                front_images = [i for i in images if "Front" in (i.get("types") or [])]

            # Save disc-specific covers: first by comment ("Disc N cover", "Disc N", "CD N", etc.)
            disc_comment_re = re.compile(
                r"(?:Disc|CD)\s*(\d+)(?:\s*cover)?",
                re.IGNORECASE
            )
            available_disc_nums: set = set()
            for img in images:
                comment = (img.get("comment") or "").strip()
                m = disc_comment_re.search(comment)
                if m:
                    try:
                        available_disc_nums.add(int(m.group(1)))
                    except ValueError:
                        pass

            leaf_indices = sorted(cd_subdirs.keys())
            overlap = sorted([d for d in leaf_indices if d in available_disc_nums])
            logmsg.verbose(
                "CAA per-disc comments found for discs={nums}; album leaf indices={leaves}; overlap={overlap}",
                nums=",".join(str(x) for x in sorted(available_disc_nums)) or "(none)",
                leaves=",".join(str(x) for x in leaf_indices),
                overlap=",".join(str(x) for x in overlap) or "(none)",
            )
            # If we have multiple discs locally but CAA has only a single front/box image,
            # warn so the user knows per-disc art will require overlay/manual work.
            # (We keep running: the root cover can still be useful.)
            if not available_disc_nums and n_wanted_leaves > 1:
                logmsg.warn(
                    "Cover Art Archive has a front image but no per-disc covers for this multi-disc album; disc folders will not get unique art (use overlay/manual artwork if desired)"
                )
            if not available_disc_nums and subfolders_only and not dry_run and n_wanted_leaves > 1:
                _LAST_PER_DISC_CAA = (0, n_wanted_leaves, True)
                return (
                    False,
                    f"per-disc CAA: selected release has no Disc/CD comment images (mbid={mbid})",
                )
            if not overlap and available_disc_nums and subfolders_only and not dry_run and n_wanted_leaves > 1:
                _LAST_PER_DISC_CAA = (0, n_wanted_leaves, True)
                return (
                    False,
                    "per-disc CAA: disc comments exist but none match leaf indices (likely folder naming isn't CD1..CDn or leaf ordering doesn't map to Disc 1..N)",
                )
            saved_discs: set = set()  # disc_num that got a cover from comment
            per_disc_bytes_written = 0
            http_failures = 0
            last_http_status: Optional[int] = None
            for img in images:
                comment = (img.get("comment") or "").strip()
                m = disc_comment_re.search(comment)
                if m:
                    disc_num = int(m.group(1))
                    if disc_num in cd_subdirs and disc_num not in saved_discs:
                        disc_url = _image_url(img)
                        if disc_url:
                            r2 = requests.get(disc_url, timeout=WEB_ART_LOOKUP_TIMEOUT)
                            if r2.status_code == 200 and not dry_run:
                                subdir = cd_subdirs[disc_num]
                                subdir.joinpath("folder.jpg").write_bytes(r2.content)
                                subdir.joinpath("cover.jpg").write_bytes(r2.content)
                                per_disc_bytes_written += len(r2.content)
                                saved_discs.add(disc_num)
                                logmsg.verbose("Downloaded disc {n} cover to {subdir}/ (from comment)", n=disc_num, subdir=subdir.name)
                            else:
                                http_failures += 1
                                last_http_status = r2.status_code

            # Fallback: if CAA has multiple front images in order (no comments), assign by position
            # front_images[0] = main/CD1, front_images[1] = CD2, front_images[2] = CD3, ...
            if len(front_images) >= len(cd_subdirs):
                for disc_num in sorted(cd_subdirs.keys()):
                    if disc_num <= len(front_images):
                        # Skip if we already saved this disc from a comment
                        if disc_num in saved_discs:
                            continue
                        img = front_images[disc_num - 1]
                        disc_url = _image_url(img)
                        if disc_url:
                            r2 = requests.get(disc_url, timeout=WEB_ART_LOOKUP_TIMEOUT)
                            if r2.status_code == 200 and not dry_run:
                                subdir = cd_subdirs[disc_num]
                                subdir.joinpath("folder.jpg").write_bytes(r2.content)
                                subdir.joinpath("cover.jpg").write_bytes(r2.content)
                                if disc_num not in saved_discs:
                                    per_disc_bytes_written += len(r2.content)
                                saved_discs.add(disc_num)
                                logmsg.verbose("Downloaded disc {n} cover to {subdir}/ (by order)", n=disc_num, subdir=subdir.name)
                            else:
                                http_failures += 1
                                last_http_status = r2.status_code

            _LAST_PER_DISC_CAA = (len(saved_discs), n_wanted_leaves, True)
            if (
                subfolders_only
                and not dry_run
                and len(saved_discs) == 0
                and n_wanted_leaves > 1
            ):
                return (
                    False,
                    f"per-disc CAA: no subfolder art written (http_failures={http_failures}, last_status={last_http_status})",
                )

            return (True, None)

        # Single cover: use front-500.jpg or fallback to metadata
        url_front = f"https://coverartarchive.org/release/{mbid}/front-500.jpg"

        last_error: Optional[str] = None
        for attempt in range(1, WEB_ART_LOOKUP_RETRIES + 1):
            try:
                r = requests.get(url_front, timeout=WEB_ART_LOOKUP_TIMEOUT)
                if r.status_code == 200:
                    if not dry_run:
                        cover_path.write_bytes(r.content)
                    return (True, None)
                if r.status_code == 404:
                    # No front cover for this release; retrying won't help
                    last_error = "no front cover in Cover Art Archive"
                    logmsg.verbose("Web art: no front cover for release {mbid} (404)", mbid=mbid)
                    break
                last_error = f"HTTP {r.status_code}"
                logmsg.verbose("Web art fetch attempt {attempt} failed: HTTP {status}", attempt=attempt, status=r.status_code)
            except Exception as e:
                last_error = str(e)
                logmsg.verbose("Web art fetch attempt {attempt} failed: {error}", attempt=attempt, error=last_error)

        # If we got 404, try fallback: release may have images but none marked "front"
        if last_error == "no front cover in Cover Art Archive":
            try:
                meta_url = f"https://coverartarchive.org/release/{mbid}"
                rm = requests.get(meta_url, timeout=WEB_ART_LOOKUP_TIMEOUT)
                if rm.status_code == 200:
                    data = rm.json()
                    images = data.get("images", [])
                    if images:
                        img = next((i for i in images if i.get("front")), images[0])
                        img_url = _image_url(img)
                        if img_url:
                            r2 = requests.get(img_url, timeout=WEB_ART_LOOKUP_TIMEOUT)
                            if r2.status_code == 200:
                                if not dry_run:
                                    cover_path.write_bytes(r2.content)
                                return (True, None)
            except Exception:
                pass  # Keep last_error as "no front cover..."

        return (False, last_error or "no cover after retries")

    except Exception as e:
        return (False, str(e))


def find_artist_images_in_folder(artist_dir: Path) -> Optional[Path]:
    """
    Find artist images in the artist folder.
    Priority order:
      1. folder.jpg (preferred for artist art)
      2. artist.jpg (secondary standard name)
      3. Any other image files (normalized - any name/type)
    
    Returns the best/largest image found, or None.
    """
    if not artist_dir.exists() or not artist_dir.is_dir():
        return None
    
    # Priority 1: Check for folder.jpg (preferred for artist art)
    folder_jpg = artist_dir / "folder.jpg"
    if folder_jpg.exists() and folder_jpg.is_file():
        return folder_jpg
    
    # Priority 2: Check for artist.jpg (secondary standard name)
    artist_jpg = artist_dir / "artist.jpg"
    if artist_jpg.exists() and artist_jpg.is_file():
        return artist_jpg
    
    # Priority 3: Look for any image files in artist folder (normalized - any name/type)
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    candidates = []
    
    for img_file in artist_dir.iterdir():
        if img_file.is_file() and img_file.suffix.lower() in image_extensions:
            # Skip standard album cover files (cover.jpg is for albums, not artists)
            # But folder.jpg and artist.jpg are already checked above
            if img_file.name.lower() != "cover.jpg":
                size_info = get_image_size(img_file)
                if size_info:
                    candidates.append((img_file, size_info))
    
    # Return largest by pixel dimensions
    if candidates:
        candidates.sort(key=lambda x: (x[1][0] * x[1][1], x[1][2]), reverse=True)
        return candidates[0][0]
    
    return None


def fetch_artist_image_from_web(artist: str, artist_dir: Path, dry_run: bool = False) -> bool:
    """
    Try to fetch artist image from MusicBrainz or other sources.
    MusicBrainz artist images are available via their API.
    Returns True on success, False otherwise.
    """
    if not ENABLE_WEB_ART_LOOKUP:
        return False
    
    try:
        init_musicbrainz()
        
        # Search for artist in MusicBrainz
        result = musicbrainzngs.search_artists(artist=artist, limit=1)
        artists = result.get("artist-list", [])
        if not artists:
            return False
        
        artist_mbid = artists[0]["id"]
        
        # MusicBrainz doesn't directly provide artist images, but we can try:
        # 1. Check if there's a relationship to an image resource
        # 2. Use external services that provide artist images based on MBID
        
        # For now, try a common pattern (this may need adjustment based on actual API)
        # Some services use: https://musicbrainz.org/ws/2/artist/{mbid}?inc=url-rels
        # Then look for image URLs in relationships
        
        # Alternative: Use Last.fm or other services that provide artist images
        # Last.fm API: http://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={artist}&api_key={key}
        
        # For now, return False - we'll need to implement based on available services
        return False
        
    except Exception as e:
        return False


def ensure_artist_images(artist_dir: Path, artist: str, dry_run: bool = False) -> None:
    """
    Ensure folder.jpg and artist.jpg exist in the artist folder.
    
    Logic:
      - If folder.jpg exists, copy it to artist.jpg (if missing) - don't overwrite folder.jpg
      - If artist.jpg exists, copy it to folder.jpg (if missing) - don't overwrite artist.jpg
      - If neither exists, search for sources and create both:
        1. Existing images in artist folder (MUSIC_ROOT/Artist/)
        2. Artist images from downloads folder (DOWNLOADS_DIR/Artist/)
        3. Artist images from overlay folder (UPDATE_ROOT/Artist/)
      - Select best/largest image and create both folder.jpg and artist.jpg
      - Convert/normalize any image file found (any name/type)
      - No web lookup (don't add missing artist art)
    """
    from config import DOWNLOADS_DIR, UPDATE_ROOT, MUSIC_ROOT
    from structured_logging import logmsg
    
    if not artist_dir.exists():
        return
    
    folder_path = artist_dir / "folder.jpg"
    artist_path = artist_dir / "artist.jpg"
    
    # Set artist context (using artist name as context)
    item_key = logmsg.begin_item(artist)
    
    try:
        # Track if we need to ensure the files exist
        need_to_ensure = True
        
        # If both exist, nothing to do (but still clean up non-standard files)
        if folder_path.exists() and artist_path.exists():
            logmsg.verbose("Both folder.jpg and artist.jpg exist, skipping")
            need_to_ensure = False
        
        # If folder.jpg exists, copy it to artist.jpg (don't overwrite folder.jpg)
        if folder_path.exists():
            if not artist_path.exists():
                if dry_run:
                    logmsg.info("Would create artist.jpg from folder.jpg")
                else:
                    logmsg.info("Creating artist.jpg from folder.jpg")
                if not dry_run:
                    shutil.copy2(folder_path, artist_path)
            need_to_ensure = False
        
        # If artist.jpg exists, copy it to folder.jpg (don't overwrite artist.jpg)
        if artist_path.exists():
            if not folder_path.exists():
                if dry_run:
                    logmsg.info("Would create folder.jpg from artist.jpg")
                else:
                    logmsg.info("%item%: Creating folder.jpg from artist.jpg")
                if not dry_run:
                    shutil.copy2(artist_path, folder_path)
            need_to_ensure = False
        
        # Find best artist image from multiple sources (only if we need to ensure files exist)
        if need_to_ensure:
            candidates = []
            
            # 1. Check existing images in artist folder (MUSIC_ROOT/Artist/)
            source_image = find_artist_images_in_folder(artist_dir)
            if source_image:
                size_info = get_image_size(source_image)
                if size_info:
                    candidates.append((source_image, size_info, "existing"))
            
            # 2. Check downloads artist folder (DOWNLOADS_DIR/Artist/)
            downloads_artist_dir = DOWNLOADS_DIR / artist_dir.name if DOWNLOADS_DIR.exists() else None
            if downloads_artist_dir and downloads_artist_dir.exists():
                downloads_image = find_artist_images_in_folder(downloads_artist_dir)
                if downloads_image:
                    size_info = get_image_size(downloads_image)
                    if size_info:
                        candidates.append((downloads_image, size_info, "downloads"))
            
            # 3. Check overlay artist folder (UPDATE_ROOT/Artist/)
            overlay_artist_dir = None
            if UPDATE_ROOT.exists():
                try:
                    rel = artist_dir.relative_to(MUSIC_ROOT)
                    overlay_artist_dir = UPDATE_ROOT / rel
                except ValueError:
                    # artist_dir is not under MUSIC_ROOT, skip overlay check
                    pass
            if overlay_artist_dir and overlay_artist_dir.exists():
                overlay_image = find_artist_images_in_folder(overlay_artist_dir)
                if overlay_image:
                    size_info = get_image_size(overlay_image)
                    if size_info:
                        candidates.append((overlay_image, size_info, "overlay"))
            
            
            # Select best (largest by pixel dimensions)
            if candidates:
                candidates.sort(key=lambda x: (x[1][0] * x[1][1], x[1][2]), reverse=True)
                best_image, best_size, source = candidates[0]
                best_pixels = best_size[0] * best_size[1]
                
                # Check if we should upgrade existing artist.jpg
                should_upgrade = True
                existing_size = None
                if artist_path.exists():
                    existing_size = get_image_size(artist_path)
                    if existing_size:
                        existing_pixels = existing_size[0] * existing_size[1]
                        if best_pixels <= existing_pixels:
                            should_upgrade = False
                            logmsg.verbose("Keeping existing artist.jpg (existing: {existing}px, new: {new}px - same or smaller dimensions)", existing=existing_pixels, new=best_pixels)
                
                if should_upgrade:
                    if artist_path.exists() and existing_size:
                        existing_pixels = existing_size[0] * existing_size[1]
                        if dry_run:
                            logmsg.info("Would upgrade artist.jpg (new: {new}px, previous: {prev}px) from {source}", new=best_pixels, prev=existing_pixels, source=source)
                        else:
                            logmsg.info("%item%: Upgrading artist.jpg (new: {new}px, previous: {prev}px) from {source}", new=best_pixels, prev=existing_pixels, source=source)
                    else:
                        if dry_run:
                            logmsg.info("Would create artist.jpg from {source}: {file}", source=source, file=best_image.name)
                        else:
                            logmsg.info("%item%: Creating artist.jpg from {source}: {file}", source=source, file=best_image.name)
                    
                    if not dry_run:
                        # Convert to JPEG if needed
                        if best_image.suffix.lower() in {".png", ".gif", ".webp"}:
                            try:
                                from PIL import Image
                                with Image.open(best_image) as img:
                                    # Convert RGBA to RGB if needed
                                    if img.mode in ("RGBA", "LA", "P"):
                                        rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                                        if img.mode == "P":
                                            img = img.convert("RGBA")
                                        rgb_img.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                                        img = rgb_img
                                    artist_path.parent.mkdir(parents=True, exist_ok=True)
                                    img.save(artist_path, "JPEG", quality=95, optimize=True)
                            except Exception as e:
                                logmsg.warn("Could not convert {file} to JPEG: {error}", file=best_image.name, error=str(e))
                                artist_path.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(best_image, artist_path)
                        else:
                            # Already JPEG - copy it
                            artist_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(best_image, artist_path)
                        
                        # Also create folder.jpg from same source (use artist.jpg, not cover.jpg)
                        if not folder_path.exists():
                            logmsg.verbose("Creating folder.jpg from artist.jpg")
                            shutil.copy2(artist_path, folder_path)
                        
                        # Clean up source file if it's from downloads or overlay (not from existing artist folder)
                        if source in ("downloads", "overlay"):
                            try:
                                best_image.unlink()
                                logmsg.verbose("Cleaned up source file: {file}", file=best_image.name)
                                logmsg.info("Cleaned up source file: {file}", file=best_image.name)
                            except Exception as e:
                                logmsg.warn("Could not delete source file {file}: {error}", file=best_image.name, error=str(e))
        
        # Clean up non-standard artist image files (anything that's not artist.jpg or folder.jpg)
        # This ensures only the standard files are synced to T8 in Step 9
        # Only clean up if both standard files exist (either they already existed or we just created them)
        if folder_path.exists() and artist_path.exists():
            image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            standard_names = {"artist.jpg", "folder.jpg"}
            
            for img_file in artist_dir.iterdir():
                if img_file.is_file() and img_file.suffix.lower() in image_extensions:
                    # Skip standard files and cover.jpg (album art, not artist art)
                    if img_file.name.lower() not in standard_names and img_file.name.lower() != "cover.jpg":
                        try:
                            if not dry_run:
                                img_file.unlink()
                            logmsg.verbose("Removing non-standard artist image: {file}", file=img_file.name)
                        except Exception as e:
                            logmsg.warn("Could not delete non-standard artist image {file}: {error}", file=img_file.name, error=str(e))
    finally:
        # Always unset item context, even if we return early or encounter an exception
        logmsg.end_item(item_key)
    
    # No artist images found - don't try web lookup (user preference: don't add missing)


def normalize_for_filename(text: str) -> str:
    """
    Normalize text for filename matching (e.g., "Pure Heroine" -> "pure-heroine").
    Converts to lowercase, replaces spaces/special chars with hyphens, removes extra hyphens.
    """
    # Convert to lowercase
    normalized = text.lower()
    # Replace spaces and common separators with hyphens
    normalized = re.sub(r'[\s_]+', '-', normalized)
    # Remove special characters except hyphens
    normalized = re.sub(r'[^a-z0-9\-]', '', normalized)
    # Remove multiple consecutive hyphens
    normalized = re.sub(r'-+', '-', normalized)
    # Remove leading/trailing hyphens
    normalized = normalized.strip('-')
    return normalized


def get_image_size(image_path: Path) -> Optional[Tuple[int, int, int]]:
    """
    Get image dimensions (width, height) and file size.
    Returns (width, height, file_size_bytes) or None if can't read.
    """
    try:
        if HAS_PIL:
            with Image.open(image_path) as img:
                width, height = img.size
                file_size = image_path.stat().st_size
                return (width, height, file_size)
        else:
            # Fallback: just use file size if PIL not available
            file_size = image_path.stat().st_size
            return (0, 0, file_size)
    except Exception:
        return None


def find_art_by_pattern(artist: str, album: str, search_dirs: List[Path]) -> List[Tuple[Path, Tuple[int, int, int]]]:
    """
    Find artwork files that match artist/album pattern (e.g., "pure-heroine-lorde.jpg").
    Returns list of (path, (width, height, file_size)) tuples, sorted by size (largest first).
    """
    if not artist or not album:
        return []
    
    # Normalize artist and album for matching
    norm_artist = normalize_for_filename(artist)
    norm_album = normalize_for_filename(album)
    
    # Pattern: album-artist.ext or artist-album.ext (with variations)
    patterns = [
        f"{norm_album}-{norm_artist}",  # "pure-heroine-lorde"
        f"{norm_artist}-{norm_album}",  # "lorde-pure-heroine"
    ]
    
    # Also try with common variations (50th-anniversary, etc. might be in filename)
    # We'll match if filename contains both normalized album and artist
    found_art = []
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        
        for art_file in search_dir.iterdir():
            if not art_file.is_file():
                continue
            
            if art_file.suffix.lower() not in image_extensions:
                continue
            
            # Skip standard art filenames (handled separately)
            if art_file.name.lower() in {"large_cover.jpg", "cover.jpg", "folder.jpg"}:
                continue
            
            # Check if filename matches pattern
            stem_lower = art_file.stem.lower()
            matches = False
            
            # Try exact pattern matches first
            for pattern in patterns:
                if pattern in stem_lower:
                    matches = True
                    break
            
            # Also check if filename contains both normalized album and artist
            if not matches:
                if norm_album in stem_lower and norm_artist in stem_lower:
                    matches = True
            
            if matches:
                size_info = get_image_size(art_file)
                if size_info:
                    found_art.append((art_file, size_info))
    
    # Sort by pixel dimensions (width * height), then by file size
    found_art.sort(key=lambda x: (x[1][0] * x[1][1], x[1][2]), reverse=True)
    return found_art


def find_predownloaded_art_source_for_album(items: List[Tuple[Path, Dict[str, Any]]]) -> Optional[Path]:
    """
    Given the list of (path, tags) for an album's tracks in DOWNLOADS_DIR,
    look in their directories for artwork files.
    
    Strategy:
      1. Find standard art files: large_cover.jpg > cover.jpg
      2. Find pattern-matched art files (e.g., "pure-heroine-lorde.jpg") by matching artist/album tags
      3. Also check DOWNLOADS_DIR itself for artwork (for browser downloads)
      4. Always select the largest image (by pixel dimensions, then file size)
      5. Prioritize root directories over subdirectories
    
    Returns the best art file Path or None.
    """
    from tag_operations import find_root_album_directory, choose_album_artist_album
    from config import DOWNLOADS_DIR
    
    # Get artist/album from tags
    items_with_tags = [(p, t) for (p, t) in items if t.get("artist") and t.get("album")]
    if items_with_tags:
        artist, album = choose_album_artist_album(items_with_tags, verify_via_mb=False)
    else:
        # No tags, can't match by pattern
        artist, album = None, None
    
    # Find root album directories (treating subdirectories as part of parent)
    all_files = [p for (p, _tags) in items]
    root_dirs = set()
    child_dirs = set()
    
    for p, _tags in items:
        root_dir = find_root_album_directory(p, all_files, DOWNLOADS_DIR)
        root_dirs.add(root_dir)
        if p.parent != root_dir:
            child_dirs.add(p.parent)
    
    # Also check DOWNLOADS_DIR itself (for browser downloads)
    search_dirs = list(root_dirs) + list(child_dirs)
    if DOWNLOADS_DIR.exists() and DOWNLOADS_DIR not in root_dirs:
        search_dirs.append(DOWNLOADS_DIR)
    
    # Collect all candidate art files with their sizes
    candidates: List[Tuple[Path, Tuple[int, int, int]]] = []
    
    # 1. Check for standard art files (large_cover.jpg, cover.jpg)
    art_priority = ["large_cover.jpg", "cover.jpg"]
    for art_name in art_priority:
        for d in sorted(root_dirs, key=lambda x: len(str(x))):
            candidate = d / art_name
            if candidate.exists():
                size_info = get_image_size(candidate)
                if size_info:
                    candidates.append((candidate, size_info))
                    break  # Found in root, don't check child dirs for this name
    
        # Check child directories if not found in root
        if not any(c.name.lower() == art_name.lower() for c, _ in candidates):
            for d in sorted(child_dirs, key=lambda x: len(str(x))):
                candidate = d / art_name
                if candidate.exists():
                    size_info = get_image_size(candidate)
                    if size_info:
                        candidates.append((candidate, size_info))
                        break
    
    # 2. Find pattern-matched art files (e.g., "pure-heroine-lorde.jpg")
    if artist and album:
        pattern_art = find_art_by_pattern(artist, album, search_dirs)
        candidates.extend(pattern_art)
    
    # 3. Select the best (largest by pixel dimensions, then file size)
    if candidates:
        # Already sorted by size in find_art_by_pattern, but re-sort all candidates
        candidates.sort(key=lambda x: (x[1][0] * x[1][1], x[1][2]), reverse=True)
        best_art = candidates[0][0]
        best_size = candidates[0][1]
        return best_art
    
    return None


def backup_audio_file_if_needed(audio_path: Path, dry_run: bool = False, backup_enabled: bool = True) -> None:
    """
    If backup_enabled is True, create a backup copy of this audio file under BACKUP_ROOT,
    mirroring MUSIC_ROOT structure. Only create if it does not already exist.
    Works for all audio file types (FLAC, MP3, M4A, etc.), not just FLAC.
    """
    if not backup_enabled:
        return
    try:
        rel = audio_path.relative_to(MUSIC_ROOT)
    except ValueError:
        return
    backup_path = BACKUP_ROOT / rel
    if backup_path.exists():
        # Backup already exists - skip to avoid overwriting original backup
        # This handles cases where file is modified multiple times (tags, then art)
        from structured_logging import logmsg
        logmsg.verbose("Backup already exists for %item%, skipping")
        return
    if not dry_run:
        import shutil
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(audio_path, backup_path)


# Alias for backward compatibility
def backup_flac_if_needed(flac_path: Path, dry_run: bool = False, backup_enabled: bool = True) -> None:
    """Alias for backup_audio_file_if_needed() for backward compatibility."""
    backup_audio_file_if_needed(flac_path, dry_run, backup_enabled)


def ensure_cover_and_folder(
    album_dir: Path,
    album_files: List[Tuple[Path, Dict[str, Any]]],
    artist: str,
    album: str,
    label: Optional[str],
    dry_run: bool = False,
    skip_cover_creation: bool = False
) -> None:
    """
    Ensure cover.jpg and folder.jpg exist, using (in order):
      - Standard pre-downloaded art (if already copied),
      - embedded art from first track,
      - web lookup via MusicBrainz.
    """
    import shutil
    
    # Normally we create album-root cover.jpg/folder.jpg. For "single-volume present" sets
    # (e.g. only VOL1 exists under an album container), prefer placing artwork in VOL1 and
    # leaving the container root empty so we don't misrepresent missing volumes.
    cover_path = album_dir / "cover.jpg"
    folder_path = album_dir / "folder.jpg"
    _reset_last_per_disc_caa()

    from structured_logging import logmsg

    def _classify_web_art_reason(reason: Optional[str]) -> Tuple[str, str]:
        """
        Return (short_kind, human_message).
        Keep this conservative: better to label "network" vs "not found" than guess wrong.
        """
        r = (reason or "").strip()
        if not r:
            return ("unknown", "unknown reason")
        rl = r.lower()
        if "timeout" in rl or "timed out" in rl:
            return ("timeout", f"timeout contacting Cover Art Archive ({r})")
        if "connection" in rl or "name or service not known" in rl or "failed to establish" in rl:
            return ("network", f"network/connection error contacting Cover Art Archive ({r})")
        if "http 404" in rl or "no front cover" in rl or "no images in cover art archive" in rl:
            return ("not_found", r)
        if "http 429" in rl:
            return ("rate_limited", f"rate limited by Cover Art Archive ({r})")
        if "http" in rl:
            return ("http_error", f"Cover Art Archive error ({r})")
        return ("error", r)

    # Determine album layout leaves once (CD*, VOL*/CD*, or VOL*).
    # For multi-disc layouts, prefer web (CAA) root cover over embedded art so a random
    # "CD1 embedded" image doesn't become the album/box cover.
    try:
        from tag_operations import album_layout_leaf_directories

        layout_leaves = album_layout_leaf_directories(album_dir) if album_dir.exists() else []
    except Exception:
        layout_leaves = []
    is_multidisc_layout = len(layout_leaves) > 1

    primary_art_dir = album_dir
    try:
        if len(layout_leaves) == 1 and layout_leaves[0] != album_dir:
            leaf = layout_leaves[0]
            if leaf.name.upper().startswith("VOL"):
                primary_art_dir = leaf
                cover_path = primary_art_dir / "cover.jpg"
                folder_path = primary_art_dir / "folder.jpg"
    except Exception:
        pass

    # Ensure root has cover and folder (skip if both already exist)
    if not (cover_path.exists() and folder_path.exists()):
        # If folder.jpg exists, copy it to cover.jpg (don't overwrite folder.jpg)
        if folder_path.exists():
            if not cover_path.exists():
                item_key = logmsg.begin_item("cover.jpg")
                logmsg.info("folder.jpg exists, creating cover.jpg from it")
                logmsg.end_item(item_key)
                if not dry_run:
                    shutil.copy2(folder_path, cover_path)
        # If cover.jpg exists, copy it to folder.jpg (don't overwrite cover.jpg)
        elif cover_path.exists():
            if not folder_path.exists():
                item_key = logmsg.begin_item("folder.jpg")
                logmsg.info("cover.jpg exists, creating folder.jpg from it")
                logmsg.end_item(item_key)
                if not dry_run:
                    try:
                        shutil.copy2(cover_path, folder_path)
                        logmsg.verbose("folder.jpg created successfully")
                    except Exception as e:
                        if label:
                            from structured_logging import logmsg
                            logmsg.warn("Failed to create folder.jpg: {error}", error=str(e))
        else:
            # Neither exists - try to create cover.jpg from embedded art or web
            if not skip_cover_creation:
                if not cover_path.exists():
                    # Multi-disc: try web first (box/front-of-book), then fall back to embedded.
                    if is_multidisc_layout and ENABLE_WEB_ART_LOOKUP:
                        logmsg.verbose("No cover.jpg; multi-disc layout detected; attempting web fetch first...")
                        ok, reason = fetch_art_from_web(
                            artist, album, cover_path, dry_run, album_dir=album_dir, subfolders_only=False
                        )
                        if ok and cover_path.exists():
                            item_key = logmsg.begin_item("cover.jpg")
                            logmsg.info("cover.jpg downloaded from web.")
                            logmsg.end_item(item_key)
                            logmsg.info("Artwork source: web (Cover Art Archive)")
                        else:
                            kind, msg = _classify_web_art_reason(reason)
                            logmsg.verbose("Web art fetch failed ({kind}): {reason}", kind=kind, reason=reason or "(none)")
                            logmsg.verbose(
                                "Multi-disc: leaving album root cover.jpg empty (not falling back to embedded art)"
                            )
                            logmsg.info("Artwork source: none (multi-disc root left empty)")
                    else:
                        # Single-disc: try web first, then fall back to embedded.
                        # This makes "delete cover.jpg to re-attempt web art" work reliably.
                        if ENABLE_WEB_ART_LOOKUP:
                            logmsg.verbose("No cover.jpg; attempting web fetch first...")
                            ok, reason = fetch_art_from_web(
                                artist, album, cover_path, dry_run, album_dir=album_dir, subfolders_only=False
                            )
                            if ok and cover_path.exists():
                                item_key = logmsg.begin_item("cover.jpg")
                                logmsg.info("cover.jpg downloaded from web.")
                                logmsg.end_item(item_key)
                                logmsg.info("Artwork source: web (Cover Art Archive)")
                            else:
                                kind, msg = _classify_web_art_reason(reason)
                                logmsg.verbose(
                                    "Web art fetch failed ({kind}): {reason}",
                                    kind=kind,
                                    reason=reason or "(none)",
                                )
                                logmsg.verbose("Falling back to embedded art after web failure...")
                                first_file = album_files[0][0]
                                if export_embedded_art_to_cover(first_file, cover_path, dry_run):
                                    item_key = logmsg.begin_item("cover.jpg")
                                    logmsg.info("cover.jpg created from embedded art.")
                                    logmsg.end_item(item_key)
                                    logmsg.info(
                                        "Artwork source: embedded (from {file})",
                                        file=first_file.name,
                                    )
                                else:
                                    logmsg.warn(
                                        "Could not obtain artwork from web ({kind}): {msg}",
                                        kind=kind,
                                        msg=msg,
                                    )
                        else:
                            logmsg.verbose("No cover.jpg; attempting to export embedded art...")
                            first_file = album_files[0][0]
                            if export_embedded_art_to_cover(first_file, cover_path, dry_run):
                                item_key = logmsg.begin_item("cover.jpg")
                                logmsg.info("cover.jpg created from embedded art.")
                                logmsg.end_item(item_key)
                                logmsg.info(
                                    "Artwork source: embedded (from {file})",
                                    file=first_file.name,
                                )
                            else:
                                logmsg.warn("Could not obtain artwork: no embedded art and web lookup disabled")
            
            # After creating/finding cover.jpg, ensure folder.jpg exists in the same directory
            if cover_path.exists() and not folder_path.exists():
                item_key = logmsg.begin_item("folder.jpg")
                logmsg.info("Creating folder.jpg from cover.jpg")
                logmsg.end_item(item_key)
                if not dry_run:
                    try:
                        shutil.copy2(cover_path, folder_path)
                        logmsg.verbose("folder.jpg created successfully")
                    except Exception as e:
                        from structured_logging import logmsg
                        logmsg.warn("Failed to create folder.jpg: {error}", error=str(e))
    else:
        # Both exist already: routine; detail/verbose only (Step 4 INFO is for new web/embedded/copies).
        # Prefer cover.jpg over folder.jpg when both exist.
        try:
            if cover_path.exists():
                logmsg.verbose("Artwork source: existing cover.jpg")
            elif folder_path.exists():
                logmsg.verbose("Artwork source: existing folder.jpg")
        except Exception:
            pass

    # Multi-disc: CAA often stores per-disc scans as *Booklet* with comments like "Disc 3 cover"
    # (not *Front*). The root cover may already exist (embedded or prior run), which previously
    # skipped web fetch entirely; fill CD/VOL leaves from the Cover Art Archive when still missing.
    if (
        album_dir.exists()
        and not skip_cover_creation
        and ENABLE_WEB_ART_LOOKUP
    ):
        from tag_operations import album_layout_leaf_directories

        leaves = album_layout_leaf_directories(album_dir)
        if len(leaves) > 0:
            missing = [
                L
                for L in leaves
                if not (L / "folder.jpg").exists() or not (L / "cover.jpg").exists()
            ]
            ref = (
                cover_path
                if cover_path.is_file()
                else (folder_path if folder_path.is_file() else None)
            )
            all_same_as_root = (
                ref is not None
                and len(leaves) > 1
                and all_leaf_folders_bytes_match_root(album_dir, leaves, ref)
            )
            # Special-case: if we only have a single disc leaf and it's not disc 1 (e.g. only CD2),
            # don't attempt per-disc CAA at all; we'll copy the album front/box art into that folder.
            import re as _re
            single_late_leaf = False
            if len(leaves) == 1:
                m = _re.search(r"\bCD\s*(\d+)\b", leaves[0].name, _re.IGNORECASE)
                if m:
                    try:
                        single_late_leaf = int(m.group(1)) != 1
                    except ValueError:
                        single_late_leaf = False
            if (missing or all_same_as_root) and single_late_leaf:
                leaf = leaves[0]
                try:
                    leaf_rel = leaf.relative_to(album_dir).as_posix()
                except Exception:
                    leaf_rel = leaf.name
                logmsg.warn(
                    "Only {leaf} is present for this multi-disc release; skipping per-disc web art lookup to avoid embedding the wrong disc cover. Disc folder will use the album front/box art unless you overlay manual artwork.",
                    leaf=leaf_rel,
                )
            else:
                # If disc folders are generic (CD1/CD2/…) and all leaves already match the album root,
                # that's a desired end-state for "same art on every disc" albums. Don't keep warning
                # or retrying per-disc CAA on every run.
                generic_disc_dirs = False
                if len(leaves) > 1 and all_same_as_root:
                    generic_disc_dirs = True
                    for p in leaves:
                        remainder = _re.sub(
                            r"^\s*CD\s*\d+\s*[-–—_:]*\s*",
                            "",
                            p.name,
                            flags=_re.IGNORECASE,
                        ).strip()
                        if remainder:
                            generic_disc_dirs = False
                            break

                if generic_disc_dirs and not missing:
                    # No action needed; keep quiet.
                    pass
                elif missing or all_same_as_root:
                    if all_same_as_root and not missing:
                        logmsg.verbose(
                            "All CD/VOL subfolder art matches album root; re-trying CAA per-disc (not stamping root to every subfolder)…"
                        )
                    else:
                        logmsg.verbose(
                            "Web art: {n} of {t} album subfolders need art; trying CAA per-disc (booklet/comment)…",
                            n=len(missing),
                            t=len(leaves),
                        )
                    ok, reason = fetch_art_from_web(
                        artist,
                        album,
                        cover_path,
                        dry_run,
                        album_dir=album_dir,
                        subfolders_only=True,
                    )
                    if not ok and reason:
                        kind, msg = _classify_web_art_reason(reason)
                        logmsg.verbose(
                            "Per-subfolder web art ({kind}): {reason}",
                            kind=kind,
                            reason=reason,
                        )
                        if reason and "per-disc CAA" in reason:
                            logmsg.warn("Could not fill per-disc art from CAA: {msg}", msg=msg)

    # Always ensure CD1/CD2/... subdirectories have folder.jpg and cover.jpg if they don't already
    if album_dir.exists():
        source_for_subfolders = None
        if cover_path.exists():
            source_for_subfolders = cover_path
        elif folder_path.exists():
            source_for_subfolders = folder_path

        if source_for_subfolders:
            from tag_operations import album_layout_leaf_directories

            n_saved, n_w, caa_tried = last_per_disc_caa_stats()
            caa_incomplete = caa_tried and n_w > 1 and n_saved < n_w and ENABLE_WEB_ART_LOOKUP
            copied_from_root_any = False
            for subdir in album_layout_leaf_directories(album_dir):
                subfolder_folder = subdir / "folder.jpg"
                subfolder_cover = subdir / "cover.jpg"
                if not subfolder_folder.exists():
                    if caa_incomplete:
                        logmsg.verbose(
                            "Not copying root art to {subdir} — CAA per-disc was incomplete for this album ({a}/{b})",
                            subdir=subdir.relative_to(album_dir).as_posix(),
                            a=n_saved,
                            b=n_w,
                        )
                    else:
                        item_key = logmsg.begin_item(f"{subdir.name}/folder.jpg")
                        logmsg.info(
                            "Creating folder.jpg in {subdir}/ from album root",
                            subdir=subdir.relative_to(album_dir).as_posix(),
                        )
                        logmsg.end_item(item_key)
                        if not dry_run:
                            try:
                                shutil.copy2(source_for_subfolders, subfolder_folder)
                                copied_from_root_any = True
                            except Exception as e:
                                if label:
                                    from structured_logging import logmsg
                                    logmsg.warn(
                                        "Failed to create folder.jpg in {subdir}/: {error}",
                                        subdir=subdir.relative_to(album_dir).as_posix(),
                                        error=str(e),
                                    )
                if not subfolder_cover.exists():
                    if caa_incomplete:
                        logmsg.verbose(
                            "Not copying root art to {subdir} — CAA per-disc was incomplete for this album ({a}/{b})",
                            subdir=subdir.relative_to(album_dir).as_posix(),
                            a=n_saved,
                            b=n_w,
                        )
                    else:
                        item_key = logmsg.begin_item(f"{subdir.name}/cover.jpg")
                        logmsg.info(
                            "Creating cover.jpg in {subdir}/ from album root",
                            subdir=subdir.relative_to(album_dir).as_posix(),
                        )
                        logmsg.end_item(item_key)
                        if not dry_run:
                            try:
                                shutil.copy2(source_for_subfolders, subfolder_cover)
                                copied_from_root_any = True
                            except Exception as e:
                                if label:
                                    from structured_logging import logmsg
                                    logmsg.warn(
                                        "Failed to create cover.jpg in {subdir}/: {error}",
                                        subdir=subdir.relative_to(album_dir).as_posix(),
                                        error=str(e),
                                    )
            # Nested VOLn/CDm: also place art on VOLn if it only contains CD subdirs
            for subdir in album_dir.iterdir():
                if not subdir.is_dir() or not re.match(
                    r"^VOL\d+", subdir.name, re.IGNORECASE
                ):
                    continue
                vol_folder = subdir / "folder.jpg"
                vol_cover = subdir / "cover.jpg"
                if not vol_folder.exists():
                    if caa_incomplete:
                        logmsg.verbose(
                            "Not copying root art to {subdir} — CAA per-disc was incomplete for this album ({a}/{b})",
                            subdir=subdir.name,
                            a=n_saved,
                            b=n_w,
                        )
                    else:
                        item_key = logmsg.begin_item(f"{subdir.name}/folder.jpg")
                        logmsg.info(
                            "Creating folder.jpg in {subdir}/ from album root",
                            subdir=subdir.name,
                        )
                        logmsg.end_item(item_key)
                        if not dry_run:
                            try:
                                shutil.copy2(source_for_subfolders, vol_folder)
                                copied_from_root_any = True
                            except Exception as e:
                                if label:
                                    from structured_logging import logmsg
                                    logmsg.warn(
                                        "Failed to create folder.jpg in {subdir}/: {error}",
                                        subdir=subdir.name,
                                        error=str(e),
                                    )
                if not vol_cover.exists():
                    if caa_incomplete:
                        logmsg.verbose(
                            "Not copying root art to {subdir} — CAA per-disc was incomplete for this album ({a}/{b})",
                            subdir=subdir.name,
                            a=n_saved,
                            b=n_w,
                        )
                    else:
                        item_key = logmsg.begin_item(f"{subdir.name}/cover.jpg")
                        logmsg.info(
                            "Creating cover.jpg in {subdir}/ from album root",
                            subdir=subdir.name,
                        )
                        logmsg.end_item(item_key)
                        if not dry_run:
                            try:
                                shutil.copy2(source_for_subfolders, vol_cover)
                                copied_from_root_any = True
                            except Exception as e:
                                if label:
                                    from structured_logging import logmsg
                                    logmsg.warn(
                                        "Failed to create cover.jpg in {subdir}/: {error}",
                                        subdir=subdir.name,
                                        error=str(e),
                                    )

            if copied_from_root_any and len(album_layout_leaf_directories(album_dir)) > 1:
                logmsg.warn(
                    "No per-disc/per-volume artwork was found; copied the album front/box artwork into disc/volume folders. Use overlay/manual artwork if you want distinct disc art."
                )


def ensure_cover_and_folder_global(dry_run: bool = False) -> None:
    """
    For every album directory under MUSIC_ROOT: ensure cover.jpg and folder.jpg
    exist (create from embedded or web if missing, else copy between them).
    Also ensures CD1/CD2 subdirs have folder.jpg from the album root.
    Single place for all cover/folder logic; used instead of per-album ensure in Step 1.
    """
    from config import AUDIO_EXT, MUSIC_ROOT
    from tag_operations import get_tags
    from logging_utils import album_label_from_tags
    from structured_logging import logmsg

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        current = Path(dirpath)
        try:
            rel = current.relative_to(MUSIC_ROOT)
            parts = rel.parts
        except ValueError:
            continue
        if len(parts) != 2:
            continue

        album_dir = current
        audio_files = []
        for r, d, f in os.walk(album_dir):
            for n in f:
                if Path(n).suffix.lower() in AUDIO_EXT:
                    audio_files.append(Path(r) / n)
        audio_files.sort()
        if not audio_files:
            continue

        first_file = audio_files[0]
        tags = get_tags(first_file) or {}
        artist = tags.get("artist", "")
        album = tags.get("album", "")
        year = tags.get("year", "")
        label = album_label_from_tags(artist, album, year)
        album_files = [(first_file, tags)]

        album_key = logmsg.begin_album(album_dir)
        ensure_cover_and_folder(
            album_dir,
            album_files,
            artist,
            album,
            label,
            dry_run=dry_run,
            skip_cover_creation=False,
        )
        logmsg.end_album(album_key)


def embed_art_into_audio_files(album_dir: Path, dry_run: bool = False, backup_enabled: bool = True) -> None:
    """
    Embed cover.jpg into each audio file in album_dir, backing up files first.
    Used for EMBED_FROM_UPDATES albums (force new art) or EMBED_ALL.
    Supports FLAC, MP3, MP4/M4A, and other formats.
    """
    from structured_logging import logmsg
    from logging_utils import album_label_from_dir
    from config import AUDIO_EXT
    from mutagen import File as MutagenFile
    from mutagen.mp3 import MP3
    
    cover_path = album_dir / "cover.jpg"
    if not cover_path.exists():
        return
    
    # Set album context for structured logging
    album_key = logmsg.begin_album(album_dir)
    
    img_data = cover_path.read_bytes()
    for dirpath, dirnames, filenames in os.walk(album_dir):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() not in AUDIO_EXT:
                continue
            
            # Prefer cover/folder.jpg from the file's directory when in a CD subfolder (disc-specific art)
            file_dir = p.parent
            if file_dir != album_dir:
                sub_cover = file_dir / "cover.jpg"
                sub_folder = file_dir / "folder.jpg"
                if sub_cover.exists():
                    img_data = sub_cover.read_bytes()
                elif sub_folder.exists():
                    img_data = sub_folder.read_bytes()
                # else keep img_data from album root (or previous dir)
            else:
                img_data = cover_path.read_bytes()
            
            item_key = logmsg.begin_item(p.name)
            backup_audio_file_if_needed(p, dry_run, backup_enabled)
            
            if dry_run:
                logmsg.info("Would embed artwork into %item% (force update)")
            else:
                logmsg.info("Embedding artwork into %item% (force update)")
                
                embedded = False
                last_error = None
                # Try FLAC first
                if p.suffix.lower() == ".flac":
                    try:
                        audio = FLAC(str(p))
                        audio.clear_pictures()
                        pic = Picture()
                        pic.data = img_data
                        pic.type = 3
                        pic.mime = "image/jpeg"
                        pic.desc = "Cover"
                        audio.add_picture(pic)
                        audio.save()
                        embedded = True
                    except Exception as e:
                        last_error = e
                
                # Try MP3
                if not embedded and p.suffix.lower() == ".mp3":
                    try:
                        audio = MP3(str(p))
                        if audio.tags is None:
                            audio.add_tags()
                        # Remove existing APIC frames
                        audio.tags.delall("APIC")
                        audio.tags.add(APIC(
                            encoding=3,  # UTF-8
                            mime="image/jpeg",
                            type=3,  # Cover (front)
                            desc="Cover",
                            data=img_data
                        ))
                        audio.save()
                        embedded = True
                    except Exception as e:
                        last_error = e
                
                # Try MP4/M4A
                if not embedded and p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                    try:
                        audio = MP4(str(p))
                        cover = MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)
                        audio['covr'] = [cover]
                        audio.save()
                        embedded = True
                    except Exception as e:
                        last_error = e
                
                # Try generic MutagenFile for other formats
                if not embedded:
                    try:
                        audio = MutagenFile(str(p))
                        if audio is not None and hasattr(audio, "pictures"):
                            audio.clear_pictures()
                            pic = Picture()
                            pic.data = img_data
                            pic.type = 3
                            pic.mime = "image/jpeg"
                            pic.desc = "Cover"
                            audio.add_picture(pic)
                        audio.save()
                        embedded = True
                    except Exception as e:
                        last_error = e
                
                if not embedded:
                    err_msg = str(last_error) if last_error else "unknown error"
                    logmsg.warn("Failed to embed artwork into %item%: {error}", error=err_msg)
                else:
                    import run_state
                    run_state.mark_embedded(p)
            
            logmsg.end_item(item_key)
    
    logmsg.end_album(album_key)


def add_missing_tags_global(dry_run: bool = False, backup_enabled: bool = True, album_dirs: Optional[List[Path]] = None) -> None:
    """
    Walk the entire MUSIC_ROOT and add missing tags to files that don't have them.
    Also fills in missing albumartist on files that have tags but blank albumartist
    (e.g. Freddie Mercury tracks) so Roon/T8 group correctly.
    Uses structured logging (begin_album, begin_item, info) so the summary shows a count.
    Only writes tags after backing up files (if backup_enabled).
    """
    from config import MUSIC_ROOT, AUDIO_EXT
    from tag_operations import (
        get_tags,
        write_tags_to_file,
        choose_album_artist_album,
        normalize_album_name,
        normalize_album_artist,
        parse_album_disc,
        is_unknown_or_bucket_artist,
    )
    from pathlib import Path
    from structured_logging import logmsg
    import os
    import re

    albums_updated = 0
    
    album_dir_filter: Optional[set] = None
    if album_dirs:
        try:
            album_dir_filter = {Path(p).resolve() for p in album_dirs}
        except Exception:
            album_dir_filter = None

    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        current_dir = Path(dirpath)

        # Only operate once per album: collapse VOLn/CDn subdirs to the parent album dir.
        try:
            from logging_utils import library_album_dir_from_abs

            parent_album_dir = library_album_dir_from_abs(current_dir)
        except Exception:
            parent_album_dir = current_dir

        if current_dir != parent_album_dir:
            continue

        # Only process real album directories (Artist/Album...). Skip MUSIC_ROOT itself and artist roots.
        try:
            rel_album = parent_album_dir.resolve().relative_to(MUSIC_ROOT.resolve())
            if len(rel_album.parts) < 2:
                continue
        except Exception:
            # If we can't determine relative depth, be conservative and skip.
            continue

        if album_dir_filter is not None:
            try:
                if parent_album_dir.resolve() not in album_dir_filter:
                    continue
            except Exception:
                continue

        audio_files = [p for p in parent_album_dir.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXT]
        if not audio_files:
            continue
        
        # Get album metadata from files that have tags
        album_metadata = None
        files_with_tags = []
        files_without_tags = []
        
        for audio_file in audio_files:
            tags = get_tags(audio_file)
            if tags and tags.get("artist") and tags.get("album"):
                files_with_tags.append((audio_file, tags))
                if not album_metadata:
                    album_metadata = {
                        "artist": tags.get("artist"),
                        "album": tags.get("album"),
                        "year": tags.get("year", ""),
                    }
            else:
                files_without_tags.append(audio_file)
        
        # Canonical album-level artist for this directory
        album_level_artist = None
        if files_with_tags:
            album_level_artist, _ = choose_album_artist_album(
                [(f, t) for f, t in files_with_tags], verify_via_mb=False
            )
        
        # Collect write actions per file (merge actions when multiple fixes apply)
        to_write_by_file: Dict[Path, Tuple[str, Dict[str, Any]]] = {}
        
        # Fill missing albumartist on files that already have tags
        if album_level_artist and files_with_tags:
            for audio_file, tags in files_with_tags:
                aa = (tags.get("albumartist") or "").strip()
                # Treat placeholder albumartist like missing (e.g. "Unknown", "Unkown", "Various", etc.)
                if (not aa) or is_unknown_or_bucket_artist(aa):
                    to_write_by_file[audio_file] = ("albumartist", tags)

        # Normalize disc totals within each VOLn (or album root) when tags are inconsistent.
        # Example: a 2-disc volume tagged as "1/1" + "2/2" should become "1/2" + "2/2".
        # Do NOT "fix" tags that are already just "1" / "2" (no total) or well-formed multi totals (e.g. 1/3, 2/3, 3/3).
        vol_groups: Dict[Optional[str], List[Tuple[Path, Dict[str, Any]]]] = {}
        for audio_file, tags in files_with_tags:
            try:
                rel = audio_file.relative_to(parent_album_dir)
                vol_part = rel.parts[0] if rel.parts else ""
            except Exception:
                vol_part = ""
            vol_key = vol_part.upper() if vol_part.upper().startswith("VOL") else None
            vol_groups.setdefault(vol_key, []).append((audio_file, tags))

        # Disc tag cleanup rules need to know if the album title indicates discs/volumes.
        album_title = (album_metadata.get("album") if album_metadata else "") or ""
        title_has_disc_hint = bool(re.search(r"\bdisc\b|\bcd\b|vol\.?|volume", album_title, re.IGNORECASE))

        for _vol_key, rows in vol_groups.items():
            # Determine target total from existing totals (only when the original tag included a "/").
            target_total = 0
            max_disc = 0
            max_track = 0
            any_disc_tag_present = False
            for _p, t in rows:
                try:
                    max_disc = max(max_disc, int(t.get("discnum") or 1))
                except Exception:
                    pass
                try:
                    max_track = max(max_track, int(t.get("tracknum") or 0))
                except Exception:
                    pass
                raw = (t.get("discnumber_raw") or "").strip()
                if raw:
                    any_disc_tag_present = True
                if "/" in raw:
                    try:
                        total_raw = int(raw.split("/", 1)[1])
                    except Exception:
                        total_raw = 0
                    if total_raw > target_total:
                        target_total = total_raw
            if target_total <= 1:
                # Single-disc album groups: keep disc tags, but make them consistent.
                # Allowed single-disc variants: "", "1", "1/1". We normalize ONLY when:
                # - only disc 1 is present, and
                # - album title has no disc/vol hint (so disc tags are purely redundant), and
                # - we see a mix of variants.
                if max_disc <= 1 and any_disc_tag_present and not title_has_disc_hint:
                    raws = [(audio_file, (tags.get("discnumber_raw") or "").strip()) for audio_file, tags in rows]
                    allowed = {"", "1", "1/1"}
                    allowed_raws = [r for (_f, r) in raws if r in allowed]
                    if allowed_raws:
                        blanks = sum(1 for r in allowed_raws if r == "")
                        ones = sum(1 for r in allowed_raws if r == "1")
                        ones_11 = sum(1 for r in allowed_raws if r == "1/1")
                        nonblank = ones + ones_11
                        # Canonical choice:
                        # - if majority blank, normalize to blank
                        # - else normalize to whichever nonblank variant is most prevalent ("1" vs "1/1")
                        #   (tie → prefer "1")
                        if blanks > nonblank:
                            canonical = ""
                        elif ones_11 > ones:
                            canonical = "1/1"
                        else:
                            canonical = "1"
                        # Only act when there is inconsistency
                        if any(r != canonical for (_f, r) in raws if r in allowed):
                            logmsg.verbose(
                                "Disc tag normalize: normalize single-disc discnumber for {group} -> {canon!r}",
                                group=_vol_key or "ALBUM_ROOT",
                                canon=canonical,
                            )
                            for audio_file, tags in rows:
                                raw = (tags.get("discnumber_raw") or "").strip()
                                if raw in allowed and raw != canonical:
                                    logmsg.verbose(
                                        "Disc tag normalize: FIX {file} ({before!r} -> {after!r})",
                                        file=audio_file.name,
                                        before=raw,
                                        after=canonical,
                                    )
                                    updated = {**tags, "discnumber": canonical}
                                    prev = to_write_by_file.get(audio_file)
                                    if prev is not None:
                                        to_write_by_file[audio_file] = (prev[0], updated)
                                    else:
                                        to_write_by_file[audio_file] = ("discnumber", updated)
                    else:
                        logmsg.verbose(
                            "Disc tag normalize: skip {group} (single-disc; no allowed discnumber variants found)",
                            group=_vol_key or "ALBUM_ROOT",
                        )
                else:
                    logmsg.verbose(
                        "Disc tag normalize: skip (no multi-disc totals found) for {group} ({count} files)",
                        group=_vol_key or "ALBUM_ROOT",
                        count=len(rows),
                    )
                continue
            # If we only ever see disc 1 in this group, we cannot safely "fix" disc totals:
            # - Some releases are truly Disc 1 of 2 (and only Disc 1 is present)
            # - Some rips misuse disc totals (e.g. 1/10) but we can't know intent without Disc 2+
            # So: never rewrite discnumber for single-disc-only groups.
            if max_disc <= 1:
                logmsg.verbose(
                    "Disc tag normalize: skip {group} (only disc 1 present; leave discnumber as-is)",
                    group=_vol_key or "ALBUM_ROOT",
                )
                continue
            # Special-case: some MP3s misuse discnumber to store track totals (e.g. "1/10" for a 10-track album).
            # If we have only disc 1 in this group and the "total" equals max track number, treat it as bogus
            # and normalize back to "1/1".
            if max_disc <= 1 and max_track > 0 and target_total == max_track and target_total >= 5:
                logmsg.verbose(
                    "Disc tag normalize: detected bogus disc total (looks like track total) for {group}: disc '1/{t}' with max_track={max_track}",
                    group=_vol_key or "ALBUM_ROOT",
                    t=target_total,
                    max_track=max_track,
                )
                for audio_file, tags in rows:
                    raw = (tags.get("discnumber_raw") or "").strip()
                    if raw == f"1/{target_total}":
                        logmsg.verbose(
                            "Disc tag normalize: FIX {file} ({before} -> 1/1)",
                            file=audio_file.name,
                            before=raw,
                        )
                        updated = {**tags, "disctotal": 1, "discnumber": "1/1", "discnum": 1}
                        prev = to_write_by_file.get(audio_file)
                        if prev is not None:
                            to_write_by_file[audio_file] = (prev[0], updated)
                        else:
                            to_write_by_file[audio_file] = ("discnumber", updated)
                continue
            # Guard: do not infer a larger total from bare disc numbers.
            # We only want to fix cases like "1/1" + "2/2" -> "1/2".
            # If some files report a disc number larger than the explicit totals (e.g. discnum=10
            # from a raw tag "10"), we can't safely normalize totals here — skip the whole group.
            if max_disc > target_total:
                logmsg.verbose(
                    "Disc tag normalize: skip {group} (max_disc={max_disc} > explicit_total={total})",
                    group=_vol_key or "ALBUM_ROOT",
                    max_disc=max_disc,
                    total=target_total,
                )
                continue
            logmsg.verbose(
                "Disc tag normalize: target total={total} for {group} (max_disc={max_disc}, files={count})",
                total=target_total,
                group=_vol_key or "ALBUM_ROOT",
                max_disc=max_disc,
                count=len(rows),
            )

            for audio_file, tags in rows:
                raw = (tags.get("discnumber_raw") or "").strip()
                if "/" not in raw:
                    # "1" / "2" / "3" are treated as OK and left unchanged.
                    logmsg.verbose(
                        "Disc tag normalize: keep {file} (raw '{raw}' has no total)",
                        file=audio_file.name,
                        raw=raw or "(empty)",
                    )
                    continue
                try:
                    disc_num = int(tags.get("discnum") or 1)
                except Exception:
                    disc_num = 1
                try:
                    total_raw = int(raw.split("/", 1)[1])
                except Exception:
                    total_raw = 0
                if total_raw == 1 and target_total > 1:
                    # Fix 1/1 -> 1/2 (or 1/N)
                    new_raw = f"{disc_num}/{target_total}"
                    logmsg.verbose(
                        "Disc tag normalize: FIX {file} ({before} -> {after})",
                        file=audio_file.name,
                        before=raw,
                        after=new_raw,
                    )
                    updated = {**tags, "disctotal": target_total, "discnumber": f"{disc_num}/{target_total}"}
                    prev = to_write_by_file.get(audio_file)
                    if prev is not None:
                        # Keep existing action label, but upgrade tags payload
                        to_write_by_file[audio_file] = (prev[0], updated)
                    else:
                        to_write_by_file[audio_file] = ("discnumber", updated)
                elif total_raw not in (0, target_total) and target_total > 1 and disc_num <= target_total:
                    # Multi-disc group: enforce a consistent total (e.g. fix 1/10 -> 1/2 when disc2 is 2/2)
                    new_raw = f"{disc_num}/{target_total}"
                    logmsg.verbose(
                        "Disc tag normalize: FIX {file} ({before} -> {after})",
                        file=audio_file.name,
                        before=raw,
                        after=new_raw,
                    )
                    updated = {**tags, "disctotal": target_total, "discnumber": new_raw}
                    prev = to_write_by_file.get(audio_file)
                    if prev is not None:
                        to_write_by_file[audio_file] = (prev[0], updated)
                    else:
                        to_write_by_file[audio_file] = ("discnumber", updated)
                else:
                    logmsg.verbose(
                        "Disc tag normalize: keep {file} (raw '{raw}' total={total} target={target})",
                        file=audio_file.name,
                        raw=raw,
                        total=total_raw,
                        target=target_total,
                    )
        
        # Add full tags to tagless files
        if album_metadata and files_without_tags:
            for audio_file in files_without_tags:
                tracknum = 0
                title = audio_file.stem
                track_match = re.match(r'^(\d+)\s*[-.]\s*', title)
                if track_match:
                    try:
                        tracknum = int(track_match.group(1))
                        title = re.sub(r'^\d+\s*[-.]\s*', '', title).strip()
                    except ValueError:
                        pass
                title = re.sub(r'^[^-]+-\s*', '', title).strip()
                if not title:
                    title = audio_file.stem
                tags_to_write = {
                    "artist": album_metadata["artist"],
                    "album": album_metadata["album"],
                    "year": album_metadata.get("year", ""),
                    "tracknum": tracknum,
                    "discnum": 1,
                    "title": title,
                }
                to_write_by_file[audio_file] = ("tags", tags_to_write)
        
        if not to_write_by_file:
            continue

        albums_updated += 1
        # Use normalized artist/album for album context so summary shows one heading
        # When raw album has [Disc N] (e.g. "... [Disc 07] The Rarities 1"), use base_album so we don't get a separate "Rarities 1" row
        artist = album_level_artist or (album_metadata["artist"] if album_metadata else "Unknown Artist")
        raw_album = album_metadata["album"] if album_metadata else "Unknown Album"
        base_album, disc_num, _ = parse_album_disc(raw_album)
        album = base_album if disc_num is not None else normalize_album_name(raw_album)
        album = album or "Unknown Album"
        artist = normalize_album_artist(artist)
        year = album_metadata.get("year", "") if album_metadata else ""
        album_key = logmsg.begin_album(artist, album, year or None)
        
        # Header is now owned by main.py (Step 1.5) so it counts and shows consistently.
        from tag_operations import update_albumartist_only, update_discnumber_only

        for audio_file, (action, tags) in to_write_by_file.items():
            item_key = logmsg.begin_item(audio_file.name)
            if action == "albumartist":
                logmsg.info("Fill albumartist: %item%")
            elif action == "tags":
                logmsg.info("Add tags: %item%")
            else:
                logmsg.info("Fix disc tags: %item%")

            aa = album_level_artist or (album_metadata["artist"] if album_metadata else None)
            if action == "albumartist":
                update_albumartist_only(audio_file, aa or "", dry_run=dry_run, backup_enabled=backup_enabled)
            elif action == "discnumber":
                update_discnumber_only(audio_file, str(tags.get("discnumber") or ""), dry_run=dry_run, backup_enabled=backup_enabled)
            else:
                write_tags_to_file(audio_file, tags, dry_run, backup_enabled, album_artist=aa)
            logmsg.end_item(item_key)
        
        logmsg.end_album(album_key)

    if albums_updated == 0:
        logmsg.info("No files needed albumartist or tag updates.")


def embed_missing_art_global(dry_run: bool = False, backup_enabled: bool = True, embed_if_missing: bool = True) -> None:
    """
    Walk the entire MUSIC_ROOT and embed cover.jpg into audio files
    that currently have no embedded artwork.
    Works with FLAC, MP3, MP4/M4A, and other formats.
    """
    if not embed_if_missing:
        return
    
    from config import AUDIO_EXT
    from logging_utils import album_label_from_dir
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3NoHeaderError
    from mutagen.mp4 import MP4
    from structured_logging import logmsg
    
    total_checked = 0
    total_embedded = 0

    # Track processed albums to avoid duplicates (e.g., CD1 and CD2 subdirectories)
    processed_albums = set()
    blocked_albums = set()
    missing_leaf_art_dirs: Dict[str, set] = {}  # album_label -> { "CD2", "VOL1", "VOL1/CD1", ... }
    
    for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
        current_dir = Path(dirpath)
        
        # Album root: strip VOLn/CDn (same as logging_utils.album_label_from_dir)
        try:
            from logging_utils import library_album_dir_from_abs

            parent_album_dir = library_album_dir_from_abs(current_dir)
        except ValueError:
            parent_album_dir = current_dir

        # Blocked albums: once per album, skip all subdirs.
        try:
            album_label_for_block = album_label_from_dir(parent_album_dir)
        except Exception:
            album_label_for_block = str(parent_album_dir)
        if album_label_for_block in blocked_albums:
            continue
        
        # Determine whether we're in a leaf (VOL/CD) folder.
        is_subdirectory = (current_dir != parent_album_dir)

        # SAFETY: Only embed artwork when it exists in the SAME directory as the audio file(s).
        # This prevents embedding the album "front/box" art into a disc folder by mistake.
        if is_subdirectory:
            local_cover = current_dir / "cover.jpg"
            local_folder = current_dir / "folder.jpg"
            if local_cover.exists():
                cover_path = local_cover
            elif local_folder.exists():
                cover_path = local_folder
            else:
                # Track missing-leaf-art directories so we can warn once per album.
                try:
                    album_label_for_warn = album_label_from_dir(parent_album_dir)
                except Exception:
                    album_label_for_warn = str(parent_album_dir)
                try:
                    rel_leaf = current_dir.relative_to(parent_album_dir).as_posix()
                except Exception:
                    rel_leaf = current_dir.name
                missing_leaf_art_dirs.setdefault(album_label_for_warn, set()).add(rel_leaf)
                continue
        else:
            cover_path = parent_album_dir / "cover.jpg"
            if not cover_path.exists():
                # No album-root art; nothing to embed at the album root.
                continue

        # If this is a multi-disc layout, require disc-level covers to exist before embedding.
        # Only log this ONCE per album (at the album root) and then block all subdirs.
        try:
            from tag_operations import album_layout_leaf_directories

            leaves = album_layout_leaf_directories(parent_album_dir) if parent_album_dir.exists() else []
        except Exception:
            leaves = []
        if len(leaves) > 1 and not is_subdirectory:
            missing_leaf_art = [
                L
                for L in leaves
                if not (L / "cover.jpg").exists() and not (L / "folder.jpg").exists()
            ]
            if missing_leaf_art:
                album_key = logmsg.begin_album(parent_album_dir)
                logmsg.warn(
                    "Skipping embed: multi-disc album is missing disc cover files in {n} subfolders (need cover.jpg or folder.jpg in each CD/VOL leaf)",
                    n=len(missing_leaf_art),
                )
                logmsg.verbose(
                    "Missing disc art in: {paths}",
                    paths=", ".join(
                        p.relative_to(parent_album_dir).as_posix() for p in missing_leaf_art[:8]
                    )
                    + (" ..." if len(missing_leaf_art) > 8 else ""),
                )
                logmsg.end_album(album_key)
                blocked_albums.add(album_label_for_block)
                continue
        
        # Use parent album directory for album context
        album_key = logmsg.begin_album(parent_album_dir)
        album_label = album_label_from_dir(parent_album_dir)

        # Warn once per album about leaf dirs that were skipped due to missing local art.
        if not is_subdirectory:
            skipped = missing_leaf_art_dirs.pop(album_label, None)
            if skipped:
                logmsg.warn(
                    "Skipping embed for leaf folders with no local artwork (need cover.jpg or folder.jpg in the same folder): {paths}",
                    paths=", ".join(sorted(skipped)),
                )
        
        # Only skip if we've already processed the parent album directory itself
        # (not subdirectories - we want to process files in both parent and subdirectories)
        if not is_subdirectory and album_label in processed_albums:
            logmsg.end_album(album_key)
            continue
        
        # Mark parent album directory as processed (only once, when we first encounter it)
        if not is_subdirectory:
            processed_albums.add(album_label)
        
        cover_data = None
        embedded_any = False

        for name in filenames:
            p = current_dir / name
            if p.suffix.lower() not in AUDIO_EXT:
                continue

            total_checked += 1
            item_key = logmsg.begin_item(p.name)

            # Check if file already has embedded art
            # Try to detect actual format (not just extension) to handle misnamed files
            has_embedded_art = False
            detected_format = None
            
            try:
                # First, try to detect actual format
                audio_test = MutagenFile(str(p))
                if audio_test is not None:
                    # Detect format from MutagenFile type
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
            use_format = detected_format or p.suffix.lower().lstrip('.')
            
            try:
                # Try FLAC first
                if use_format == 'flac' or p.suffix.lower() == ".flac":
                    try:
                        audio = FLAC(str(p))
                        if len(audio.pictures) > 0:
                            has_embedded_art = True
                            logmsg.verbose("%item% already has embedded art, skipping")
                    except Exception:
                        # Not actually FLAC, try other formats
                        pass
                
                # Try MP3
                if not has_embedded_art and (use_format == 'mp3' or p.suffix.lower() == ".mp3"):
                    try:
                        from mutagen.mp3 import MP3
                        audio = MP3(str(p))
                        if audio.tags:
                            # Check for APIC frames (cover art)
                            for key in audio.tags.keys():
                                if key.startswith("APIC"):
                                    has_embedded_art = True
                                    logmsg.verbose("%item% already has embedded art, skipping")
                                    break
                    except Exception:
                        pass
                
                # Check MP4/M4A for embedded art
                if not has_embedded_art and (use_format == 'mp4' or p.suffix.lower() in {".m4a", ".mp4", ".m4v"}):
                    try:
                        audio = MP4(str(p))
                        if 'covr' in audio:
                            has_embedded_art = True
                            logmsg.verbose("%item% already has embedded art, skipping")
                    except Exception:
                        pass
                
                # Generic check for other formats
                if not has_embedded_art:
                    try:
                        audio = MutagenFile(str(p))
                        if audio is not None:
                            if hasattr(audio, "pictures") and len(audio.pictures) > 0:
                                has_embedded_art = True
                                logmsg.verbose("%item% already has embedded art, skipping")
                    except Exception:
                        pass
                        
            except Exception as e:
                logmsg.warn("Could not check embedded art for %item%: {error}", error=str(e))
                # Don't skip - try to embed anyway if we can determine format later

            if has_embedded_art:
                logmsg.end_item(item_key)
                continue

            # Check if MP4/M4A already has embedded art
            if p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                try:
                    audio = MP4(str(p))
                    if 'covr' in audio:
                        has_embedded_art = True
                        logmsg.verbose("%item% already has embedded art, skipping")
                except Exception:
                    pass  # Will try to embed below
                
                if has_embedded_art:
                    logmsg.end_item(item_key)
                    continue

            if cover_data is None:
                try:
                    cover_data = cover_path.read_bytes()
                except Exception as e:
                    logmsg.warn("Could not read cover.jpg: {error}", error=str(e))
                    logmsg.end_item(item_key)
                    break

            embedded_any = True

            if dry_run:
                logmsg.info("[DRY RUN] Would embed art into %item% (missing embedded art)")
                total_embedded += 1
                logmsg.end_item(item_key)
                continue

            # Keep console output concise: one info line per file when embedding succeeds.
            logmsg.verbose("Embedding art into %item% (missing embedded art)")

            backup_audio_file_if_needed(p, dry_run, backup_enabled)

            # Embed art based on detected or actual file type
            # Try to detect actual format first (handles misnamed files)
            embedded = False
            last_embed_error = None  # Accumulate for single warning at end
            try:
                # Try FLAC first (if extension or detected format suggests it)
                if use_format == 'flac' or p.suffix.lower() == ".flac":
                    try:
                        audio = FLAC(str(p))
                        pic = Picture()
                        pic.data = cover_data
                        pic.type = 3
                        pic.mime = "image/jpeg"
                        pic.desc = "Cover"
                        audio.clear_pictures()
                        audio.add_picture(pic)
                        audio.save()
                        logmsg.info("Embedded art into %item% (FLAC)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() == ".flac":
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            last_embed_error = error_msg
                            logmsg.verbose("File has .flac extension but is not valid FLAC, trying other formats: {error}", error=error_msg)
                        else:
                            raise
                
                # Try MP3 if FLAC didn't work
                if not embedded and (use_format == 'mp3' or p.suffix.lower() == ".mp3"):
                    try:
                        from mutagen.mp3 import MP3
                        audio = MP3(str(p))
                        if audio.tags is None:
                            audio.add_tags()
                        audio.tags.add(APIC(
                            encoding=3,  # UTF-8
                            mime="image/jpeg",
                            type=3,  # Cover (front)
                            desc="Cover",
                            data=cover_data
                        ))
                        audio.save()
                        logmsg.info("Embedded art into %item% (MP3)")
                        total_embedded += 1
                        embedded = True
                    except ID3NoHeaderError:
                        # File has no ID3 tags, add them
                        audio = MP3(str(p))
                        audio.add_tags()
                        audio.tags.add(APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=cover_data
                        ))
                        audio.save()
                        logmsg.info("Embedded art into %item% (MP3)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() == ".mp3":
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            last_embed_error = error_msg
                            logmsg.verbose("File has .mp3 extension but is not valid MP3, trying other formats: {error}", error=error_msg)
                        else:
                            raise
                
                # Try MP4/M4A if not already embedded
                if not embedded and (use_format == 'mp4' or p.suffix.lower() in {".m4a", ".mp4", ".m4v"}):
                    try:
                        audio = MP4(str(p))
                        # MP4 files store artwork in the 'covr' atom
                        # Create MP4Cover object with JPEG data
                        cover = MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)
                        audio['covr'] = [cover]
                        audio.save()
                        logmsg.info("Embedded art into %item% (MP4/M4A)")
                        total_embedded += 1
                        embedded = True
                    except Exception as e:
                        if p.suffix.lower() in {".m4a", ".mp4", ".m4v"}:
                            error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                            if len(error_msg) > 200:
                                error_msg = error_msg[:197] + "..."
                            last_embed_error = error_msg
                            logmsg.verbose("Could not embed art into MP4/M4A file %item%: {error}", error=error_msg)
                        else:
                            raise
                
                # Try generic MutagenFile for other formats
                if not embedded:
                    try:
                        audio = MutagenFile(str(p))
                        if audio is not None:
                            # Try to add art (format-specific)
                            if hasattr(audio, "add_picture"):
                                pic = Picture()
                                pic.data = cover_data
                                pic.type = 3
                                pic.mime = "image/jpeg"
                                pic.desc = "Cover"
                                audio.add_picture(pic)
                                audio.save()
                                logmsg.info("Embedded art into %item% (generic)")
                                total_embedded += 1
                                embedded = True
                            else:
                                logmsg.warn("Format {ext} does not support embedded art", ext=p.suffix)
                    except Exception as e:
                        error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                        if len(error_msg) > 200:
                            error_msg = error_msg[:197] + "..."
                        last_embed_error = error_msg
                        logmsg.verbose("Could not embed art using generic method: {error}", error=error_msg)
                
                if not embedded:
                    if last_embed_error:
                        logmsg.warn("Could not embed art into %item%: {error}", error=last_embed_error)
                    else:
                        logmsg.warn("Could not determine format or embed art into %item%")
                else:
                    import run_state
                    run_state.mark_embedded(p)
            except Exception as e:
                # Don't log the exception object directly (may contain binary data)
                error_msg = str(e).split('\n')[0] if str(e) else "unknown error"
                # Truncate very long error messages
                if len(error_msg) > 200:
                    error_msg = error_msg[:197] + "..."
                logmsg.warn("Failed to embed art into %item%: {error}", error=error_msg)
            
            logmsg.end_item(item_key)

        # Events tracked automatically by structured logging
        
        logmsg.end_album(album_key)
    
