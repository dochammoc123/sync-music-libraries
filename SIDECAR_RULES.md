## Sidecar artwork rules (`cover.jpg` / `folder.jpg`)

This document describes how this project decides when/how to create sidecar artwork files and how it treats artwork found in Downloads.

For orientation (normal run, Mp3tag, overlay, tray) without deep rules, see [USER_GUIDE.md](USER_GUIDE.md).

### Plain-language summary

Think in terms of **where the audio files actually sit**, not MusicBrainz “how many discs the release had.”

1. **All tracks directly in the album folder** — the layout detector finds **no** `CD*` / `VOL*` **leaves**. That is the only “simple flat album.” You need `**cover.jpg` + `folder.jpg` at album root** for Downloads-cleanup completeness, and embedding uses `**album_root/cover.jpg`** only (not `folder.jpg` alone).
2. **Any subfolder leaf layout** (`CD2` alone, `**CD1`** alone, `**VOL1**` alone, `**VOL1/CD3**` alone, `**CD1`+`CD2`+…**, Lilith-style trees, …) — **same rule bucket**. If putting tracks under `**CD*` / `VOL*` (or nested `VOL*/CD*`) shaped the folders, that counts as multi-folder for this project: Downloads images need to collapse to **one logical image family** before auto-assign, **album root artwork** follows the guarded “root/box vs disc” logic, **embedding** skips the whole album until **every leaf you have on disk** has **at least** `cover.jpg` **or** `folder.jpg`, and **completeness** for lifting preservation requires **both** files **inside each leaf** (with one leaf present, “every leaf” is just **that folder**).
3. **Naming hints** (“Vol. 2” in folder names, etc.) can still tighten log warnings for subset imports — they **do not** switch you between buckets 1 vs 2; **folder shape** decides that.

### Consistency rules (manual fixes & `UPDATE_ROOT` overlay)

Use this for anything you copy by hand (including overlay). Pipeline behavior stays in buckets 1–2 above; this section is **one human rulebook** so `CD*` / `VOL*` / nested `**VOL*/CD*`** doesn’t feel like three different hobbies.

1. **Leaf / track sidecars:** Put `**cover.jpg`** and `**folder.jpg**` in **every folder that actually contains `.flac` (or other) audio.** Flat album ⇒ that folder is the album root. Multi-folder ⇒ **each leaf** (`CD2/`, `**VOL3/`** with tracks only there, `**VOL1/CD2/**`, …). Nested trees use the **same** rule—the leaf is just deeper; there is **no separate** “nested” policy.
2. **Box art at album root:** Filesystem placement is your choice. **Step 4** only runs the album-root “box” pipeline (**consistent embedded across leaves**, else **CAA**) for albums **imported from Downloads in that same run** (Step 1 → Step 4). **Later runs:** if the album root has **neither** `cover.jpg` nor `folder.jpg`, Step 4 **skips** that box attempt and **leaves the root empty** (stable “no box, only discs”). **Flat** albums (no `CD*`/`VOL*` leaves) still use the normal root embedded/web rules every run. When **any** leaf exists, **downloads preservation / completeness** still turns on **each leaf**, not forced root filler.
3. **Non-leaf parents** (e.g. `**VOL1/`** when audio lives only under `**VOL1/CD1/**`): **Not required** by this script. Putting art there is **explicitly optional** (some folder-based players); **never** substitute that for leaf sidecars.
4. `**UPDATE_ROOT`:** Mirror `**MUSIC_ROOT`** exactly—same `**Artist/Album/…/leaf**` path and filenames (use `**cover.jpg**` when triggering overlay cover tracking).
5. **Tray / log location:** Prefer running `**library_tray_launcher.py`** from the `**sync-music-libraries**` folder **or** ensure `**main.py`** resolves from `**…/scripts/sync-music-libraries/**`. If the launcher lives in `**…/iCloudDrive/scripts/**` alone, it will find `**sync-music-libraries\\main.py**` when present. Log files are **not** meant to live under iCloud (see `config.LOGS_DIR` and `README`).

---

### Terms

- **Sidecars**: filesystem files named `cover.jpg` and `folder.jpg` next to the tracks (album root or a disc/volume subfolder).
- **Embedded art**: pictures stored **inside** the audio file tags (FLAC/APIC/`covr`, etc.).
- **Leaf folder / layout leaf**: a folder that holds audio for **one slice** of a multi-folder layout — one disc row in the folder tree as we detect it. Leaves are **top-level** `CD*`, or `**VOL*` when it contains no nested `CD*`** (tracks live straight under `VOL*`), or **nested `VOL*/CD*`** when the volume splits into CDs. `**VOL1/` with `VOL1/CD1/` and `VOL1/CD2/**` → leaves `**VOL1/CD1**`, `**VOL1/CD2**` (`VOL1` itself is not a leaf).
- **Completeness** (downloads cleanup / lifting preservation until Step 10): **No leaf folders detected** → need **album root `cover.jpg` + `folder.jpg`**. **One or more leaves** → **each present leaf** needs **both** sidecars (`album_root` not counted when any leaves exist). **Exactly one leaf** ⇒ “each leaf satisfied” simply means **that folder** has both images.
- **Backup mirror**: `BACKUP_ROOT` contains copies of originals (same relative path as `MUSIC_ROOT`). When present, it is the source of truth for “original embedded art”.

### High-level pipeline placement

- **Step 1 (Downloads processing)** may copy artwork from Downloads into the library **only when unambiguous**.
- **Step 4 (Ensure cover/folder artwork)** is the primary place where missing sidecars are created (embedded/web), respecting multi-disc safety rules.
- **Step 5.1 / 5.2 / 5.3 (Embed artwork)** — see below; image bytes always come from **local sidecars next to each track’s folder**.
- **Downloads cleanup** should not delete potentially meaningful assets when artwork mapping is ambiguous.

### Step 1: Downloads → library artwork behavior (conservative for multi-disc)

When processing a newly-downloaded album:

- **Single-disc / simple layout**
  - The script may choose a “best” image from the Downloads album folder (standard names, pattern matches, largest dimensions) and copy/upgrade `cover.jpg` (and then `folder.jpg`) into the library album folder.
- **Multi-disc / volume layout in Downloads**
  - If the Downloads album folder contains `CD`* or `VOL`* subfolders **and any images exist**, the script **does not attempt to auto-assign** which image goes to which disc/volume.
  - It logs a **warning** and leaves all images in Downloads for manual review.
  - Step 4 still runs normally afterwards (embedded/web rules).

### Step 4: Ensure sidecars exist (single-disc vs multi-disc)

Step 4 is implemented in `artwork.ensure_cover_and_folder()` and runs globally via `artwork.ensure_cover_and_folder_global()`.

#### Always (common behavior)

- If only one of `cover.jpg` / `folder.jpg` exists in the target directory, the other is created by copying the existing one.

#### Album root artwork when layout has **no** leaves (tracks at album folder only)

When `cover.jpg` is missing:

- **Prefer embedded art first**:
  - Scan album tracks and read embedded art bytes.
  - For each track: if a backup-mirror file exists under `BACKUP_ROOT`, read embedded art from the backup copy; otherwise read from the live file.
  - The **first track** that yields embedded bytes is used to create `cover.jpg`.
- If embedded cannot supply a cover, and web lookup is enabled: fetch from **Cover Art Archive** (MusicBrainz).
- If web fails: fall back to embedded export logic.

#### Subfolder leaf layout (**one leaf or many** — root vs leaves)

If `album_layout_leaf_directories` finds **any** leaf, Step 4 treats **album root** “front/box cover” separately from **each leaf**.

- **Album root / box/front** (subfolder leaf layout)
  - **New this run from Downloads:** try **consistent embedded across leaves** (backup mirror when present) for the root; if not consistent, try **web (CAA)** for a front/box image. If web fails, root may stay empty.
  - **Not imported from Downloads this run:** if root has **no** `cover.jpg` **and** no `folder.jpg`, **do not** re-run that box pipeline—keep root empty so “no box, only discs” stays stable.
  - If **only one** of the two sidecars exists at root, the usual rule still applies: copy it to create the other (no web required).
- **Leaf folders (`CD1/`, `CD2/`, `VOL1/`, etc.)**
  - Each leaf behaves like single-disc when its own sidecars are missing:
    - Prefer embedded from tracks inside that leaf (backup mirror when present, else live).
    - If `cover.jpg` is created, create `folder.jpg` from it.
  - If leaf art is still missing after embedded/web attempts, the script warns and **does not** copy root art into the leaf (to avoid mis-assignment and accidental embedding).
- **CAA “front” at album root with `VOL1/CD*`, `VOL2+` siblings:** CAA returns one image for the release; it may be box art or first-volume art. The script logs a **warning** and, when `VOL1` contains `CD*` subfolders, may mirror the same bytes into **missing** `VOL1/CD*/cover.jpg` and `folder.jpg` only (never auto-filling `VOL2+` from this heuristic).

### “Original vs updated embedded” rule (backup overlay)

- If a backup-mirror file exists for a track, Step 4 treats that backup copy as the source of truth for “original embedded art”.
- If no backup exists for a track, the live file is assumed to be original for that track.

### Post-Step 4 warning for newly processed albums

After Step 4 completes, the script warns for albums that were touched in Step 1 (Downloads) when required sidecars are still missing next to audio-bearing root/leaf directories. This is meant to surface “Lilith-style” ambiguity where manual fixes are expected.

### Step 5.1–5.3: Embedding into audio tags

Order in `main.py` (so overlay force-embed is not repeated by the missing-art pass):

| Step | When | Behavior |
|------|------|----------|
| **5.1** Embed from updates | `--mode embed` and album got `cover.jpg` from `UPDATE_ROOT` this run | Force-embed **all** audio under that album (`embed_art_into_audio_files`) |
| **5.2** Embed missing | `normal` / `embed` (`EMBED_IF_MISSING`) | Only files that **still lack** embedded art (`embed_missing_art_global`) |
| **5.3** Embed all | `--embed-all` (hidden) | Force-embed whole library from sidecars — use sparingly |

#### Step 5.2 details (`embed_missing_art_global`)

- **Which files:** Supported audio extensions (`AUDIO_EXT`); only those that **already have no** embedded picture (FLAC pictures, MP3 `APIC`, MP4 `covr`, plus Mutagen fallbacks).
- **Which folder’s image:** **Strictly the directory that contains the audio file.** Tracks under `CD2/` never use album-root sidecars automatically — use `**cover.jpg`** there if present, else `**folder.jpg**`. Tracks sitting **directly under the album folder** use `**album_root/cover.jpg` only** (if there is only `folder.jpg` at root, Step 5.2 does not embed from it).
- **Subfolder-layout embed gate:** When **any** leaf folder exists (including **exactly one** leaf), embedding runs only after **every such leaf on disk** has **at least one** of `**cover.jpg` / `folder.jpg`**. If a leaf still has **neither**, the whole album is skipped for embedding. **Flat album** (**zero** leaves) never hits this gate.
- **Backups:** Optional backup of each audio file before writing tags (`BACKUP_ORIGINAL_FLAC_BEFORE_EMBED` in normal/embed modes). **Step 8 backup sync** removes a backup only when the live file at the same relative path is identical or missing (orphan)—see **Backup mirror** in [USER_GUIDE.md](USER_GUIDE.md) for the **rename/move** caveat (orphan removal if you do not update `BACKUP_ROOT` paths).

#### Step 5.1 details (overlay force)

For albums that gained `cover.jpg` from `UPDATE_ROOT`, `embed_art_into_audio_files()` embeds **all** audio under that album tree from sidecars (subfolders prefer their local `cover.jpg` / `folder.jpg`). Runs **before** Step 5.2 so those tracks are not counted as “missing” and rewritten again.

### Downloads cleanup rules (preserve when ambiguous)

- Processed audio files are removed from Downloads.
- Known garbage files (cleanup extensions / known junk filenames) are removed.
- **Non-audio assets** in subfolders (PDFs, cues, logs, scans, etc.) are preserved by default (manual review).
- When multi-disc/volume artwork is ambiguous or incomplete, image assets are preserved in Downloads for manual review.

**How Step 10 knows what to keep:** When an album is processed in Step 1, the script stores **norm-cased paths under `DOWNLOADS_DIR`** where that import “lived” (from `find_root_album_directory`, i.e. walking up from each file it moved), plus a few **nearby spillover folders** common for scans (same parent / same top-level as that pocket). Nothing re-matches by artist/album name at cleanup time—it’s purely those registered paths (**inside, equal to, or an ancestor** of guarded trees). `**library_album_dir`** ties each guarded path to its **library folder** only so preservation can lift after **every layout leaf there** has `**cover.jpg` + `folder.jpg`**.

**When may leftover JPGs in Downloads be removed?**

- **Single-disc (flat album folder):** After the run, if the library album folder has both `cover.jpg` and `folder.jpg` (from Downloads copy, embedded export, or web in Step 4), leftover images in that download tree are eligible for cleanup in Step 10.
- **Subfolder leaf layout (`CD`*/`VOL*`/nested, one leaf or several):** The download tree stays **preserved** until **each present leaf folder** under the album has **both** `cover.jpg` and `folder.jpg` (one leaf ⇒ that single folder counts). Album root-only completeness applies only when **no** such leaves exist. Step 10 re-checks after Step 4 before lifting preservation.
- If mapping was ambiguous in Step 1 (e.g. multiple image families), images remain preserved until the library is complete as above.