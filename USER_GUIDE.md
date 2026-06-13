# User guide — typical usage

Plain overview of **how you normally run the sync**. For artwork edge cases, multi-disc quirks, or Step-by-step internals, see [SIDECAR_RULES.md](SIDECAR_RULES.md).

---

## What you configure once

Paths live in `**config.py`**: downloads folder, main library (`**MUSIC_ROOT`**), `**UPDATE_ROOT**` overlay, backup root, portable sync target (`T8_ROOT`) if used, and logging. Until those match your PC, runs will go to the wrong places.

---

## Typical “new music” workflow

1. **Drop** ripped or purchased files under your **downloads music** folder (`DOWNLOADS_DIR`—often `Downloads\Music\…`).
2. **Run** `**python main.py --mode normal`** (or **Run (normal)** from the tray launcher).
3. When it finishes, check the **summary** line (“warnings / errors”). If nothing scary, you’re fine.
4. The script **moves** audio into `**Artist → Album`** on the main library volume, lays out `**CD*`** / `**VOL*`** folders when tags call for them, pulls in **overlay** patches, tries to align **covers** (`cover.jpg` / `folder.jpg`) and **embed** missing artwork, drops MP3/AAC copies when FLAC exists, then **syncs** to portable if configured.

Dry run (no writes): `**python main.py --mode normal --dry`**.

### Multi-volume series — layout rules (by design)

When separate imports normalize to **one album folder**, layout follows **album tag text** (disc tags optional):

| Tag pattern | Folders | Examples |
|-------------|---------|----------|
| Plain album title (no Vol.) | **Album root** | Segovia vol. 1; “Greatest Hits” without “Vol. 1” |
| `, Vol. N` or `, Vol. N - Subtitle` | **`VOLn/`** flat (no `CDm` inside) | Segovia Collection, Earl Klugh “Vol. 2” |
| `Vol. N: Subtitle` + optional `Disc M` | **`VOLn/CDm`** when both apply | Doo Wop box, Lilith-style sets |
| Multi-disc, no Vol. in title | **`CD1`, `CD2`…** at album root | Kenny Rogers “Disc 1” / “Disc 2” |

#### Volume 1 at album root (library precedent)

**By design**, volume **1** often lives at the **album root** and only **later volumes** get **`VOL2/`**, **`VOL3/`**, … subfolders. That is not a bug and not something the script “fixes” on later runs.

**Why:** the first import is usually tagged **without** “Vol. 1” (plain *Greatest Hits*). A later import says *Greatest Hits Vol. 2* → **`VOL2/`**. The script will **not** move tracks already at the root into **`VOL1/`** when a sibling volume appears (too easy to mis-guess).

**On-disk example** (existing library):

```
Elton John\(1970 - 1986) Greatest Hits\
  *.flac, cover.jpg, folder.jpg     ← vol. 1 (no “Vol. 1” in tags)
  VOL2\
    *.flac, cover.jpg, folder.jpg   ← tagged “… Vol. 2”
```

Same pattern elsewhere: Segovia Collection (root + **`VOL2`** + **`VOL4`**), Earl Klugh (*Best of…* at root, *…, Vol. 2* under **`VOL2/`**).

**Your choice:** keep that asymmetry (shorter path for vol. 1), or **once** move root tracks into **`VOL1/`** (and sidecars) if you want every volume under **`VOLn/`**. No automatic library-wide repair pass.

**Already processed albums** keep their on-disk layout until you re-import or move by hand.

---

## Mp3tag (manual checks — usually read-only)

Use **Mp3tag** to **look**, not necessarily to edit. Point it at:

1. **An unmodified copy** (your own backup **before** you put files in Downloads) → see **what the script will infer** about artist, album, discs, embedded art.
2. **The files under `MUSIC_ROOT` after a run** → see **what landed** (tags side-by-side with folder layout).

Same checklist either way—no changes unless you intend to fix something: **Artist**, **Album**, **DISCNUMBER / DISCTOTAL**, **TRACK**, **YEAR**, embedded-cover preview.

**Changing tags** is optional; do it only when tags are objectively wrong.

### Re-running from scratch vs lightweight fixes

- **Full reprocess:** delete that album folder from `**MUSIC_ROOT`**, put your clean originals back under `**DOWNLOADS_DIR`** (same idea as before the first run), run **normal** again. Use this when layout, filenames, or tags need the full pass.
- `**UPDATE_ROOT` overlay:** for **quick fixes**, especially **artwork** (`cover.jpg` here and there)—no need to wipe the library copy or refill Downloads.

---

## Sidecar artwork (looking at `cover.jpg` / `folder.jpg`)

**Sidecars** are normal image files **next to the audio** (album folder and/or `CD*` / `VOL*` subfolders). After a sync, open them in your file browser or image viewer — same as any other JPG.

- **Flat album:** `cover.jpg` / `folder.jpg` sit in the album folder; that’s the art the script and many players use for the whole album.
- **Multi-disc layout:** each **leaf** folder (where the `.flac` files live) can have its own pair; the **album root** may also have a pair that acts as a **box / compilation front** when present.

If a root image looks wrong (random CAA front, first-disc art you don’t want as a “box”), you can **remove the box at the album root only**: delete **both** `**cover.jpg`** and `**folder.jpg`** in the **album** folder, **not** inside `VOL1/CD1` etc. Leave leaf folders alone unless you mean to change disc art.

On **later** normal runs, the script **won’t** keep re-pulling web art for that empty **multi-disc** root—stable “discs only, no box” (details in [SIDECAR_RULES.md](SIDECAR_RULES.md)). Use `**UPDATE_ROOT`** overlay if you want a chosen image back at album root.

**Flat** album (all tracks at album root): deleting those two files removes album-level sidecars; a future run may refill them from embedded/web because that layout is treated differently—see **SIDECAR_RULES** if you rely on an empty flat root.

---

## Downloads folder — removals and leftovers

Clean-up is deliberate, not sloppy:

- **Processed** tracks are cleared from Downloads when safe.
- **Multi-disc layouts with unclear artwork**: images may stay until each **disc folder** (`CD*`, `**VOL*`**, `**VOL*`/`CD*`, …) eventually has `**cover.jpg`** and `**folder.jpg**` in the library. Don’t bulk-delete leftovers until you’ve read warnings or skimmed [SIDECAR_RULES.md](SIDECAR_RULES.md).

If Downloads still has a whole album pocket after runs, warnings often explain why.

---

## Update overlay (small fixes—mainly artwork)

**Overlay** is easiest when `**MUSIC_ROOT` already looks right** and you only want to patch files (typically **covers**) without ripping the album back out.

Your overlay folder (`**UPDATE_ROOT`**, `_UpdateOverlay` next to master storage in typical setups) mirrors the library:

```
_UpdateOverlay /
  ArtistName /
    (YYYY) AlbumName /
      cover.jpg
      optionally other files (replacement FLACs, etc.)
```

- Run **normal** (or embed): Step 2 **copies** overlay files into `**MUSIC_ROOT`** at that same path **and deletes** them from `_UpdateOverlay` after applying.
- **Normal** fills **embedded** art only where files **currently have no picture** in tags.

---

## Embed mode (overlay covers **into FLAC tags**)

- `**python main.py --mode embed`** — same pipeline as normal, plus a pass that pushes `**cover.jpg` from overlay-applied folders** **into FLAC (and backups)** after Step 5 for albums that gained a library cover via overlay **this run**.
- `**--mode normal`** alone does **not** overwrite existing embedded art with your new `**cover.jpg`**.

Rule of thumb:

- Overlay **only**: **normal**.
- Overlay **cover** meant to **overwrite** what's inside the FLAC: **embed** (backups respected per config).

Restore / recovery: `**--mode restore`** (uses backup originals; skips embedding)—see `**main.py`** help if you rely on backups.

### Backup mirror (`BACKUP_ROOT`) — what gets removed

Step 8 (**sync backups**) walks `**BACKUP_ROOT`** and deletes a backup file **only** when:

- The **live file** exists at the **same relative path** under `**MUSIC_ROOT`** and compares **identical** to the backup (so the backup adds no value), or  
- The live file is **missing** — the backup is treated as **orphaned** and removed so dead paths do not linger.

**Rename / move caveat:** Backups are keyed by **relative path**, not identity metadata. If you **rename or move** a track in `**MUSIC_ROOT`** but leave the backup at the **old** path, the next backup sync sees **no** live file there and **deletes** that backup as orphan. If you need to keep pre-move snapshots, **move or copy the backup file to the new relative path** under `BACKUP_ROOT` yourself, or archive copies outside the mirror.

---

## Tray launcher

Prefer running `**library_tray_launcher.py`** from inside the `**sync-music-libraries**` folder **or** from your **scripts** parent if `**sync-music-libraries`** lives beneath it—that way `**main.py`**, icons, and venv resolve consistently. Tray uses the same `**main.py`** as the CLI.

---

## Logs (where to look)

Windows default: `**%LOCALAPPDATA%\sync-music-libraries\logs\`** (`library_sync_detail_*.log`, summary file). Exact names and overrides: `**README.md`** and `**config.LOGS_DIR`**. Each console run prints paths at startup.

---

## More reading


| Topic                                                          | Document                             |
| -------------------------------------------------------------- | ------------------------------------ |
| **Sidecars, multi-disc, box vs discs, Downloads preservation** | [SIDECAR_RULES.md](SIDECAR_RULES.md) |
| **Installation & venv**                                        | [PREREQUISITES.md](PREREQUISITES.md) |
| **Technical overview & artifacts**                             | [README.md](README.md)               |


Entry point code: `**python main.py --help`**.