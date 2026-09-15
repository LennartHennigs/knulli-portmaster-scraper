# CLAUDE.md — knulli-portmaster-scraper

Guidance for working in this repo. Read alongside `README.md` (user-facing) and
`CHANGELOG.md` (what shipped).

## What this is

A single-file, stdlib-only Python tool (`pmscraper.py`) that gives PortMaster
ports proper artwork/descriptions/genres in KNULLI's (Batocera-fork) Ports menu.
It does **not** query a scraper service: PortMaster already wrote every port's
metadata (`port.json`) and downloaded its artwork (`images_pm/`) onto the SD
card. The tool *transcribes* that into `/userdata/roms/ports/gamelist.xml`. It
works fully offline; `--online` only tops up covers from PortMaster's repo.

Root cause it solves: neither PortMaster nor ES ever turns a port's own
metadata — `port.json`, or a porter-shipped `gameinfo.xml` — into a KNULLI
gamelist entry, no matter which files the port ships.

Text-field precedence, when a port has more than one source available:
`gameinfo.xml` (porter-authored editorial text, present on most but not all
installed ports — see below) > `port.json` > `--online` catalog. Art
precedence: `images_pm` cache > cover shipped in the port's own directory >
`--online` download.

## Scope / non-goals

- **In scope:** offline transcription from `port.json` + `images_pm/`; optional
  `--online` top-up from PortMaster's `ports.json` catalog + GitHub artwork;
  non-destructive idempotent gamelist merge; classification/reporting of what it
  can't identify; on-device install + auto-run.
- **Out of scope (by design):** scraping non-PortMaster games (no data for
  them — they're reported `unknown` and left untouched); pushing metadata via
  ES's HTTP POST API (undocumented JSON schema — rejected for v1); shipping a
  pre-generated gamelist.

## Hard-won facts about the device (verified, not assumed)

The target is an **Anbernic RG40XXH running KNULLI**. Verified against the
user's actual SD card (mounted at `/Volumes/ROMs`, **ext4**) and KNULLI source:

- **KNULLI's ES is its own fork:** `knulli-cfw/batocera-emulationstation`,
  branch `knulli` (pinned in `knulli-cfw/distribution` →
  `package/batocera/emulationstation/.../batocera-emulationstation.mk`).
- **ES event-script contract** (verified in that fork's `es-app/src/FileData.cpp`,
  `launchGame`):
  - `game-start` → `fireEvent("game-start", rom, basename, getName())`:
    `$1`=escaped rom path, `$2`=basename **stem** (no extension), `$3`=name.
  - `game-end` → `fireEvent("game-end")` — **NO arguments.**
  - ⇒ A lone `game-end` hook cannot know what ran. We use a **marker pattern**:
    `game-start` drops `/tmp/pmscraper.trigger` when PortMaster launches;
    `game-end` acts only if the marker exists. Don't "simplify" this back into
    one hook.
- **Event scripts need the exec bit.** On exFAT/NTFS userdata ES silently won't
  run them (auto-run dies); the Ports-menu launcher still works. KNULLI's ext4
  SD default is fine. `install.sh` detects and warns.
- **System Python is 3.11** (`/usr/lib/python3.11`). Target ≥3.7. `ET.indent`
  (3.9+) is used behind `hasattr`.
- **PortMaster lives at** `$XDG_DATA_HOME/PortMaster`
  (= `/userdata/system/.local/share/PortMaster` on device). Artwork cache:
  `config/images_pm/<name_cleaner(port)>.{screenshot,cover}.<ext>`.
- **Real port launchers** are `#!/bin/bash`, source `$controlfolder/control.txt`,
  and end with `pm_finish`. `control.txt` sets `CUR_TTY=/dev/tty0`. The Ports
  launcher here follows that skeleton so output is visible and env is correct.
- **ES HTTP API** on `127.0.0.1:1234` (source: `es-app/src/services/HttpServerThread.cpp`;
  `isAllowed` → localhost only unless `PublicWebAccess`): `GET /reloadgames`
  re-reads gamelists from disk (avoids ES clobbering our file at its own exit);
  `POST /notify` = a text popup (`displayNotificationMessage`). **`/notify`
  renders only while ES is foreground** — NOT during a port launched from the
  Ports menu (ES is backgrounded then). There is **no progress-bar endpoint**
  (that widget is ES's internal `GuiScraperRun`); `--progress` fakes it with
  per-port toast text. All best-effort.
- **A toast can still be delivered from a Ports-menu launch — just not inline.**
  The installer hands the message to a detached background waiter
  (`toast_later` in `installer/installer-stub.sh`) that watches ES go down for
  the restart, waits for `GET /` to answer again, sleeps a few seconds for the
  UI to reach the foreground, then POSTs `/notify`. The child survives the
  launcher exiting and the ES restart — **verified on device**. Before this the
  install looked like a bare reboot. Poll slowly (2–3s): ES is re-reading a
  ~1400-entry gamelist off SD at exactly that moment.
- **On-screen output during a launched port = pugwash** (PortMaster's pygame GUI;
  runtime repo `PortsMaster/PortMaster-GUI`). Drive it via `PortMasterDialog.txt`:
  `PortMasterDialogInit "no-harbour"` → `PortMasterDialog "progress" msg done total`
  / `"message"` (escaped `\n` → newline) / `progress_clear` → `PortMasterDialogExit`.
  ONLY way to draw during a Ports-menu run (ES backgrounded → toasts don't render).
  The launcher feeds it `--emit-progress` PMPROG lines.
- **Restart/reload ES:** `restart_es()` uses `knulli-es-swissknife` (NOT
  `batocera-es-swissknife`), falling back to `GET /quit` (KNULLI's boot loop
  relaunches ES). HTTP `GET /restart` REBOOTS THE DEVICE — don't use it.
  `/reloadgames` from a launched port (ES backgrounded) won't repaint until ES is
  foreground → tell the user to run **Update Gamelists** (or reboot).
- **Network / covers:** `images_pm` on a fresh card holds **screenshots only
  (0 covers)** — but most ports ship their **own cover file directly in their
  port directory** (`<portdir>/cover.*`; verified 38/45 installed ports on the
  user's card, including four — `descent`, `descent2`, `doom3`, `masseffect`
  — whose `port.json` doesn't declare a `covers[]` entry at all, so even
  `--online` couldn't find one before `local_cover()` was added). `--online`
  is still the last resort for the remainder: PortMaster has covers for ~52%
  of ports (712/1348) upstream. `<image>`=screenshot, `<thumbnail>`("Box")=
  cover — keep strict, NEVER a screenshot in the box slot (user correction).
  Launcher uses `--online` (degrades cleanly offline → `Temporary failure in
  name resolution`); auto-run hook stays offline. On macOS, `--online` needs
  `SSL_CERT_FILE=/etc/ssl/cert.pem`; the device's own certs work.
- **gamelist entries carry `id` attributes and pre-existing metadata** from ES's
  own ScreenScraper runs — the merge MUST stay non-destructive (see below).

## Architecture (pmscraper.py)

Flow in `main()`: discover dirs → `index_images` → `scan_installed_ports`
(also picks up each port's `gameinfo.xml`, if any, via `parse_gameinfo`, and
stashes the port's own directory as `info["_portdir"]`) →
`enumerate_es_entries` (walk like ES: `.sh`/`.squashfs`, skip dot-entries and
the top-level tool launchers in `SKIP_LAUNCHERS`) → `classify` into buckets →
resolve art (`resolve_art` → `local_cover` before `download_art`) +
`build_fields` (layers `gameinfo.xml` text over `port.json`'s, field by
field) + `completeness` → merge → `print_summary`/`write_report` →
`reload_es`.

**Buckets:** `port.json` / `catalog` (stem matches upstream archive name, needs
`--online`) / `fuzzy` (stem matches upstream *title*) / `unknown` / `stale`.
**Completeness:** `missing` / `partial` (has entry, no `image` or no `desc`) /
`complete`. Exit code `1` when any `unknown` exists.

**Selectors/actions:** `--since EPOCH` = only ports whose `port.json` is newer
(auto-run's "new ports only"; game-start records `date +%s`, game-end passes it);
`--emit-progress` = PMPROG lines on stdout + log→stderr, for the pugwash launcher;
`--register-tools` = tidy gamelist entries for PortMaster + both Scraper
launchers (`TOOL_ENTRIES`, non-destructive, present-launchers-only, needs
`--apply`); `--unregister-self` = drop both of the scraper's own entries
(`SCRAPER_LAUNCHERS`, install.sh --uninstall). Two Ports-menu launchers: plain
"PortMaster Scraper" = full scan, non-destructive (fills gaps only);
"PortMaster Scraper (Rescan All)" = same but with `--force` (overwrites
everything). Hooks = new-only (`--since`). **Exit codes:** 0 ok, 1 unknowns
present, 2 crash/setup error.

### Invariants — do not break

- **Non-destructive merge.** Only `MANAGED_TAGS` are written; existing non-empty
  values are kept unless `--force`. `favorite`/`playcount`/`lastplayed`/`hidden`
  are never touched. Dry run is the default; only `--apply` writes.
- **Stale = file truly absent on disk**, not "absent from our filtered
  enumeration." (We exclude `PortMaster.sh` from enumeration but it exists on
  disk and is legitimately in the gamelist — `find_stale` keys on
  `(ports_dir/path).exists()` so `--prune` never deletes it. Regression fixed
  once; keep it.)
- **`attr.image` may be a bare string**, not a `{screenshot, covers}` dict
  (e.g. `descent`, `descent2`). `download_art` normalizes it; never call
  `.get()` on it unguarded (it crashed the whole `--apply` run once).
- **`SKIP_LAUNCHERS = frozenset(TOOL_ENTRIES)`** — the tool launchers
  (`PortMaster.sh`, `PortMaster Scraper.sh`, `PortMaster Scraper (Rescan
  All).sh`) are skipped in enumeration; else the scraper reports *itself* as
  `unknown` and forces exit 1 on every auto-run. Add a tool launcher to
  `TOOL_ENTRIES` and it's skipped + registered from that one place.
- **`gameinfo.xml` is porter-shipped, not something PortMaster's own scraper
  writes** — a `<gameList><game>` doc some porters put directly in
  `<portdir>/`, alongside `port.json`, with real editorial text. Only look for
  it right there, never recursively (`freesynd/data/gameinfo.xml` on a real
  card is an unrelated file that happens to share the name, nested two levels
  deeper than any real one — a glob that recursed would pick it up wrongly).
  Its `<image>` tag is untrustworthy (sometimes a cover, sometimes a
  screenshot, no way to tell which) — `parse_gameinfo` ignores it; art stays
  on the `port.json`/`local_cover`/`images_pm`/`--online` pipeline.
- **`name_cleaner` must mirror** `harbourmaster.util.name_cleaner` or artwork
  lookups misalign.
- **stdlib `xml.etree` is intentional** (KNULLI ships no `defusedxml`; we only
  parse gamelists the device itself wrote). A security hook will flag this —
  it's a documented, accepted trade-off (see the import comment). Don't add a
  dependency to satisfy it.
- Back up to `.bak`, write via temp + `os.replace`; malformed input → `.broken`.
- **`index_by_path` assumes unique `<path>`.** It's a dict, so duplicate `<game>`
  entries with the same path collapse to the last one — the earlier duplicate is
  then invisible to scrape/`find_stale`/`--prune` (never updated or removed).
  Duplicates shouldn't occur (ES writes one entry per path); low-severity, but
  don't build logic that relies on seeing every node for a path.

## Installing from a Mac card reader

`install.sh` is device-only (absolute `/userdata/...` paths). To install onto a
mounted card, copy to card-relative paths under the mount (`/Volumes/ROMs` =
`/userdata`): `system/.local/share/PortMaster/pmscraper/`, `roms/ports/` (the
launcher), `system/configs/emulationstation/scripts/{game-start,game-end}/`.
The card is ext4 (Paragon `UFSD_EXTFS4`), so `chmod 755` sticks. Logs land at
`…/PortMaster/pmscraper.log` + `pmscraper-report.md`.

## Testing / verification

- `python3 -m unittest discover -s tests` — 26 tests, a throwaway synthetic
  KNULLI tree, no network, no device. Covers apply/dry-run/idempotence/force/
  online(via seeded cache)/malformed and every bucket + completeness path.
- `python3 -m py_compile pmscraper.py`; run once under `python3 -W error`.
- Shell: `sh -n` the POSIX scripts; the launcher is `#!/bin/bash` (process
  substitution) so check it with `bash -n`, not `sh -n`.
- Against a real card: **read-only dry run first** —
  `python3 pmscraper.py --ports-dir /Volumes/<card>/roms/ports`.
- Verify a refactor is **behavior-neutral** against the card: `git stash`, run
  the dry-run, compare output byte-for-byte. `cmp -s <repo> <card>` confirms
  installed files match.

## Gotchas

- **macOS `--online` fails** with an SSL cert-verify error (local Python/certifi
  quirk) — not a tool bug; it degrades to offline. Tests exercise `--online`
  via a seeded local `pmscraper_ports.json` cache instead of the network.
- macOS writes `._*` AppleDouble shadow files onto the ext4/exFAT card; both ES
  and `enumerate_es_entries` skip dot-entries, so they're harmless.
- On the user's card, `Animal Crossing.sh` and `Half-Life 2 Episode 2.sh` are
  from **other sources** (no `port.json`) — correctly `unknown`; leave them.
- Never `--apply` to the user's real card without explicit consent.
