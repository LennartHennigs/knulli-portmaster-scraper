# PortMaster scraper for KNULLI (RG40XXH)

## Context

Ports installed through PortMaster show up in KNULLI's Ports menu as bare `.sh`
filenames with no artwork, description, or genre. The normal scrapers
(ScreenScraper / TheGamesDB, and Skraper offline) can't help: they index ROMs by
name/hash, and a `Sonic 3 AIR.sh` shell script matches nothing in their databases.

Meanwhile PortMaster already has every bit of that metadata, and has already put
most of it on the SD card. Nothing needs to be scraped from the internet at all —
it needs to be *transcribed* from PortMaster's own files into
`/userdata/roms/ports/gamelist.xml`.

Goal: a small, self-contained tool on the device that reads PortMaster's data and
writes a correct KNULLI gamelist, runs automatically after PortMaster exits, and
never destroys hand-edited metadata or favourites.

## What was verified (not assumed)

Read directly from PortMaster's and KNULLI's own source:

| Fact | Source |
|---|---|
| Each installed port writes `<ports>/<portdir>/port.json` with `attr.title`, `desc`, `inst`, `genres`, `porter`, plus a `files` map naming its `.sh` launchers | `harbourmaster/harbour.py` — `load_ports()`, install writes `port.json` |
| All port artwork is **already cached on the SD card** at `…/PortMaster/config/images_pm/<port>.screenshot.png` and `.cover.png` (PortMaster downloads an 87 MB `images.zip`) | `harbourmaster/source.py` — `_load_images()` |
| Filenames there are normalised by `name_cleaner()`: lowercase, strip punctuation, collapse spaces/dots to `.` | `harbourmaster/util.py` |
| Upstream catalog: 1386 ports — **100 % have a screenshot, 100 % have genres**, 53 % have cover art, most have community ratings | downloaded and counted from `PortMaster-Info/ports.json` |
| PortMaster *does* have a gamelist writer for KNULLI, but it only fires for ports shipping a `gameinfo.xml` — **0 of 1386 ports ship one**. This is the root cause. | `harbourmaster/platform.py` — `PlatformKnulli(PlatformBatocera)`, `gamelist_add()` |
| KNULLI's gamelist lives at `/userdata/roms/ports/gamelist.xml` | `PlatformBatocera.gamelist_file()` |
| KNULLI's ES exposes an **HTTP API on `127.0.0.1:1234`**, always on, including `GET /reloadgames` — the same code path as the menu's "Update Gamelists" | `es-app/src/services/HttpServerThread.cpp:520`, started unconditionally at `main.cpp:620` |
| ES only rewrites `gamelist.xml` on exit for *dirty* systems (launching a port dirties `ports` via playcount) — so writing the file behind a running ES and then letting it quit normally **would** clobber it | `SystemData.cpp:1276-1303`, `main.cpp:822` |
| Valid gamelist tags include `name desc genre tags image thumbnail marquee titleshot fanart rating releasedate developer publisher players favorite hidden` — `thumbnail` is labelled **"Box"** in the metadata editor | `es-app/src/MetaData.cpp:30-100` |
| The `ports` system is `extensions: [sh, squashfs]`, and ES **recurses into subfolders**, skipping hidden entries and dropping folders that end up empty | `es_systems.yml:2629`, `SystemData.cpp:298-398` |

A working prototype exists (`pmscraper.py`, written during planning) and was tested
against a synthetic KNULLI tree: it correctly merged 3 installed ports + 1
hand-installed port into an existing gamelist, copied artwork, preserved
`<favorite>` and `<playcount>`, and was idempotent on re-run. It needs to be
re-created in this repo (the prototype lived in a cloud scratchpad that did not
teleport) and then extended per *Coverage* below.

## Options considered

| # | Approach | Verdict |
|---|---|---|
| 1 | Offline transcriber: read `port.json` + `images_pm/`, write `gamelist.xml` | **Recommended.** No network, no API keys, instant, works for every installed port |
| 2 | Same, plus `--online` top-up from `ports.json` + raw GitHub screenshots | **Included as a flag.** Covers hand-installed ports and a missing artwork cache |
| 3 | Push metadata into ES via `POST /systems/ports/games/…` HTTP API instead of editing XML | Rejected for v1 — undocumented JSON schema, needs ES running. Revisit later |
| 4 | Pre-generate a gamelist off-device and ship it | Rejected — goes stale, ignores which ports *you* actually installed |
| 5 | Custom ScreenScraper-side database of ports | Rejected — not ours to maintain, and the data already exists locally |

## Answering "isn't that a Knulli setting?" (artwork)

Partly. KNULLI's **Scraper → Image source / Box source / Logo source** settings
control what *ES's own scraper* downloads from ScreenScraper and where it files
it. They do not re-map a gamelist that some other tool wrote, so they won't help
here — but they also mean we don't have to choose. ES stores `image` and
`thumbnail` ("Box") as independent slots and each theme decides which it renders.

So the tool fills **both**: screenshot → `<image>`, cover art → `<thumbnail>`,
plus `<titleshot>` as an alias for the screenshot. A `--prefer-covers` flag swaps
the first two for themes that only render `<image>`. No decision needed up front.

## Recommended implementation

New public repo (**this folder**: `/Users/lennart/Documents/Development/portmaster-scraper`),
suggested GitHub name `knulli-portmaster-scraper`.

### Layout

```
portmaster-scraper/
├── pmscraper.py                 # the whole tool, stdlib only, py3.7+
├── install.sh                   # copies files into place on the device
├── ports/
│   └── PortMaster Scraper.sh    # launchable from the Ports menu
├── hooks/
│   └── pmscraper-hook.sh        # ES game-end hook (auto-run)
├── README.md
└── CHANGELOG.md
```

### `pmscraper.py`

Single file, standard library only (KNULLI ships Python 3). Flow:

1. **Locate dirs.** `$HM_PORTS_DIR` → `/userdata/roms/ports` → `/roms/ports` →
   `/storage/roms/ports`. Config dir found by looking for `images_pm/`, mirroring
   `harbourmaster/config.py`'s own candidate list.
2. **Enumerate and classify** — walk the tree the way ES does, then scan
   `<ports>/*/port.json` and resolve each port's `.sh` launchers from
   `items` / `items_opt` / `files`. Anything left over goes to the catalog
   matcher, then the report. See *Coverage* below.
3. **Index artwork** from `images_pm/`, keyed by `name_cleaner(port name)` — a
   verbatim reimplementation of PortMaster's function so lookups line up.
4. **Optional `--online` top-up**: `ports.json` from the PortMaster release (cached
   locally), and per-port screenshots from `raw.githubusercontent.com`. Also
   recovers hand-installed ports that have no `port.json` by matching `.sh` stems
   against the catalog.
5. **Copy media** to `<ports>/images/<sh-stem>-image.<ext>` / `-thumb.<ext>`,
   skipping unchanged files.
6. **Merge into `gamelist.xml`** — parse existing, index by `<path>`, update only
   the tags we own, leave everything else alone. Back up to `.bak`, write via
   temp file + atomic `os.replace`.
7. **Reload ES** — `GET http://127.0.0.1:1234/reloadgames`. This is what avoids
   the clobber problem: ES re-reads from disk immediately instead of overwriting
   our file at its next exit.

Flags: `--apply` (default is a dry run), `--force`, `--online`, `--prefer-covers`,
`--port-dates`, `--only-missing`, `--report FILE`, `--csv`, `--stub-unknown`,
`--prune`, `--no-fuzzy`, `--ports-dir`, `--cfg-dir`, `--no-reload`, `--restart-es`.

Exit code is `0` when everything resolved, `1` when there were `unknown` entries —
so the hook can decide whether to bother you about it.

### Running it against the real SD card — read-only

Now that this session runs on your machine (teleported), the dry run can point
straight at a mounted card and I can read the output directly.

- **Dry run is the default.** With no `--apply`, the tool opens files read-only,
  writes nothing, copies no artwork, touches no gamelist, and makes no network
  calls unless `--online` is passed. It prints the full classification report and
  exits. `--apply` is the only thing that ever writes.
- **From a card reader:** pure stdlib Python + explicit `--ports-dir`, so pop the
  SD into the Mac and point at the mounted partition:

  ```
  python3 pmscraper.py --ports-dir /Volumes/SHARE/roms/ports
  ```

- **Detection understands the card layout.** On the device KNULLI's SHARE
  partition is `/userdata`, so PortMaster's config is at
  `/userdata/system/.local/share/PortMaster/config`. Read from a card reader that
  is `<mountpoint>/system/.local/share/PortMaster/config`. When `--ports-dir` ends
  in `roms/ports` the tool derives the partition root and looks there before
  falling back to `$XDG_DATA_HOME` — so the card-reader path finds the artwork
  cache with no `--cfg-dir`.

### Coverage: find everything, update what's stale, report what it can't identify

The tool does not just walk PortMaster's installed ports and stop there. It first
enumerates the **full set of entries ES itself would show**, then works out which
of those it can explain.

**1. Enumerate like ES does.** Walk the ports tree for `*.sh` and `*.squashfs`,
skipping dot-files and dot-folders, mirroring `SystemData::populateFolder`.
Top-level hits are ordinary ports; nested hits (a `.sh` inside a port's own
payload directory) are flagged separately, because ES shows those too and they
are usually noise a user would rather hide.

**2. Classify every entry** into one of five buckets:

| Bucket | Meaning | Action |
|---|---|---|
| `port.json` | PortMaster-installed, metadata on disk | scrape (offline) |
| `catalog` | no `port.json`, but the `.sh` stem matches upstream `ports.json` | scrape (needs `--online`) |
| `fuzzy` | stem matches an upstream **title** after normalisation rather than the archive name | scrape, but list it in the report so you can confirm |
| `unknown` | no match anywhere — not a PortMaster port | never touched; **reported** |
| `stale` | a `<game>` entry in gamelist.xml whose `.sh` no longer exists | reported; removed only with `--prune` |

**3. "Non-updated" detection.** For each identifiable entry the tool compares the
gamelist record against what it could write and classes it `missing` (no entry at
all), `partial` (entry exists but has no `image`, or no `desc`), or `complete`.
Because the merge is idempotent, the default run already fixes `missing` and
`partial` and leaves `complete` untouched. `--only-missing` narrows a run to
entries that are missing or partial, which is the fast path for the auto-run hook.

**4. Report.** Every run — including a dry run — ends with a summary, and
`--report FILE` writes the full breakdown as Markdown (or CSV with `--csv`):

```
scraped   28  (24 from port.json, 3 from catalog, 1 fuzzy)
skipped   11  already complete
unknown    4  not found in PortMaster
stale      1  gamelist entry with no matching file

unknown ports (not from PortMaster — scrape these yourself or edit by hand):
  ./Doom64EX.sh                 has metadata already
  ./my-test-launcher.sh         no metadata
  ./Cave Story/cavestory.sh     nested — ES will show this inside a folder
  ./retroarch-extra.sh          no metadata
```

For `unknown` entries the tool does nothing to the gamelist by default — they may
be hand-curated already, and silently overwriting them would be exactly the wrong
behaviour. `--stub-unknown` opts in to writing just a tidied `<name>` (underscores
and dashes to spaces, title-cased) so they at least read as titles rather than
filenames; everything else is left for ES's normal scraper or manual editing.

### Metadata mapping

| gamelist tag | Source | Note |
|---|---|---|
| `name` | `attr.title` | `.sh` stem instead when a port has several launchers |
| `desc` | `attr.desc` + `attr.inst` | install notes appended unless just "Ready to run." |
| `genre` | `attr.genres` | title-cased, with FPS/RPG fixups |
| `tags` | `attr.genres` | raw, for ES filtering |
| `developer` | `attr.porter` | who ported it |
| `publisher` | literal `PortMaster` | makes ports filterable as a group |
| `rating` | `rating.average_rating / max_rating` | ES wants 0.0–1.0 |
| `image` | `images_pm/<port>.screenshot.*` | → `<ports>/images/` |
| `thumbnail` | `images_pm/<port>.cover.*` | ES calls this slot "Box" |
| `releasedate` | `source.date_added` | **opt-in** (`--port-dates`) — it's the port's date, not the game's |

Never written: `favorite`, `playcount`, `lastplayed`, `hidden`, `kidgame`.
Existing non-empty values are preserved unless `--force`.

### Auto-run

ES fires event scripts from `<userES>/scripts/<event>/` (confirmed in
`es-core/src/Scripting.cpp`), where `<userES>` is
`/userdata/system/configs/emulationstation`.

`install.sh` places `hooks/pmscraper-hook.sh` in `scripts/game-end/`. On each
game exit it:

1. checks whether the thing that just exited was `PortMaster.sh` — bails out in
   milliseconds otherwise, so normal gameplay is unaffected;
2. runs `pmscraper.py --apply --only-missing --report <log>`;
3. calls `/reloadgames` only if something actually changed, then `POST /notify`
   with a short toast — "6 ports scraped" or "6 scraped, 2 unidentified",
   so unmatched ports surface on the handheld instead of only in a log file.

This ordering is what makes auto-run safe: ES re-reads the file we just wrote
before it ever gets a chance to write its own in-memory copy over it. `game-end`
hooks run async, which is fine here — nothing else is competing for the file.

### `install.sh`

Run once over SSH (`./install.sh`), idempotent, and re-runnable to upgrade.

1. **Preflight.** Confirm `python3` exists and is ≥3.7; locate the ports dir and
   PortMaster config dir using the same logic as the tool and print both for
   confirmation; refuse to continue if the ports dir looks wrong.
2. **Detect the filesystem.** `stat -f -c %T` on the ports partition. exFAT is
   common on KNULLI and [cannot carry Unix permissions or symlinks][exfat] — so
   on exFAT the script does not pretend `chmod +x` worked: it verifies with a
   test rather than assuming, and installs the launcher in a way that does not
   depend on the exec bit (ES invokes ports through `sh`, so this is fine).
3. **Install files.**
   - `pmscraper.py` → `<tools>/PortMaster/pmscraper/pmscraper.py` — beside
     PortMaster's own files, on a partition that survives updates, and *not* in
     `/roms/ports` where ES would list a stray `.sh`.
   - `PortMaster Scraper.sh` → `<ports>/` — the only file that intentionally
     lands where ES can see it, so you can run it from the handheld.
   - `pmscraper-hook.sh` → `/userdata/system/configs/emulationstation/scripts/game-end/`,
     creating the directory if ES hasn't yet.
4. **Set rights.** `chmod 755` on the three scripts, then read the mode back and
   warn (not fail) if the filesystem silently dropped it.
5. **Verify.** Run `pmscraper.py` once in dry-run mode and show the summary, so
   installation ends with proof it can actually see your ports.
6. **`--uninstall`** removes all three files and leaves `gamelist.xml`, the
   `images/` folder, and the `.bak` untouched. `--dry-run` prints the plan.

[exfat]: https://knulli.org/guides/portmaster-and-exfat/

## Verification

Local (this machine), before anything touches the device:

1. Build a synthetic KNULLI tree (`fake/userdata/roms/ports` + a populated
   `images_pm/`, seeded from the real upstream `ports.json`) and assert:
   dry run writes nothing; `--apply` produces well-formed XML; `<favorite>` and
   `<playcount>` survive; a second run is a no-op; `--force` does overwrite;
   `--online` recovers a port that has no `port.json`; a malformed existing
   gamelist is preserved as `.broken` rather than lost.
2. Seed that tree with the awkward cases and assert the classification is right:
   a non-PortMaster `.sh` (reported `unknown`, gamelist untouched); a nested
   `.sh` inside a port payload dir (reported, flagged nested); a gamelist entry
   whose `.sh` was deleted (reported `stale`, only removed under `--prune`); an
   entry with a `<name>` but no `<image>` (classed `partial`, and picked up by
   `--only-missing`); a complete entry (classed `complete`, skipped). Check the
   exit code is `1` whenever an `unknown` is present.
3. Re-parse the output with `ElementTree` and check every tag name against the
   list in `MetaData.cpp`.
4. `python3 -m py_compile`, and run once under `python3 -W error`.

Against the real card (now possible — this session is local):

5. **Read-only first:** `python3 pmscraper.py --ports-dir /Volumes/<card>/roms/ports`.
   Nothing is written. Review the classification, especially `unknown` / `fuzzy`.
6. `./install.sh` on the device (or dry-run against the mounted card).

On the RG40XXH:

7. `curl -s localhost:1234/reloadgames` on its own first — confirms the API is
   reachable and unauthenticated locally before the tool depends on it.
8. `python3 pmscraper.py --apply`, then check the Ports menu shows art and
   descriptions without an ES restart.
9. Install a fresh port via PortMaster, exit, and confirm the `game-end` hook
   scrapes it automatically.
10. Reboot and confirm nothing was lost, and that `gamelist.xml.bak` exists.

## Risks / open items

- **ES API auth.** `/reloadgames` goes through an `isAllowed()` check. Locally it
  should pass, but step 7 above confirms it before we rely on it; `--restart-es`
  is the fallback.
- **`--force` overwrites hand-edited names.** Documented; it is not the default.
- **Multi-launcher ports** (e.g. a game plus its expansion) get the `.sh` stem as
  the display name rather than the port title. Reasonable, but worth a look on
  real data.
- The 87 MB `images.zip` is only present if PortMaster has fetched it; `--online`
  covers the case where it hasn't, downloading just the screenshots needed.
- **Fuzzy matching can be wrong.** Matching a `.sh` stem against upstream *titles*
  will occasionally pick a near-namesake. That's why `fuzzy` is its own bucket in
  the report rather than being folded into `catalog` — and why `--no-fuzzy` exists
  if you'd rather see those listed as `unknown` instead.
- **Non-PortMaster ports stay unscraped**, by design — the tool has no data for
  them. The report is the deliverable there. If the list turns out to be long, a
  follow-up could add a small user-maintained `overrides.json` that feeds the same
  merge path, so hand-written metadata survives re-runs.
