# Changelog

## v1.2.0

On-device polish: graphical progress, smarter auto-run, and release hardening.

### Added
- **On-screen progress bar.** The Ports-menu launcher drives PortMaster's own
  GUI (`pugwash`) over its dialog FIFO to show a native `name [x/y]` progress
  bar - the only way to draw during a launched port (ES is backgrounded, so
  `/notify` toasts don't render). `pmscraper --emit-progress` streams
  machine-readable `PMPROG` lines on stdout (human log → stderr) to drive it.
- **`--progress`** now posts just `name [m/n]` (the ASCII bar was dropped - it
  renders badly in a popup); visible only while ES is foreground (auto-run hook,
  SSH). Progress is reported during the download phase, where `--online` spends
  its time.
- **`--since EPOCH`** scrapes only ports whose `port.json` is newer - the
  auto-run's "new ports only" path. `game-start` records the launch time,
  `game-end` passes it (with a 2s guard for coarse-mtime/exFAT). The Ports-menu
  launcher stays a full scan.
- **`--register-tools`** gives the tool launchers (PortMaster + the Scraper)
  tidy gamelist entries (name/desc/genre=Utility/publisher) instead of bare
  filenames; non-destructive, present-launchers-only, run by `install.sh`.
- The Ports launcher now runs `--online` (cover/box art is not in the on-card
  cache) and degrades cleanly offline.
- Exit code **2** now signals an unexpected error or a setup/config error
  (bad `--ports-dir`, ports dir not found), distinct from `1` (unidentified
  launchers present) - so callers can tell a failure from "unknowns".
- `install.sh --uninstall` now removes the scraper's own gamelist entry
  (`--unregister-self`); PortMaster's entry and the gamelist are left intact.

### Fixed
- `download_art` crashed on ports whose `attr.image` is a bare string, not a
  `{screenshot, covers}` dict (e.g. `descent`, `descent2`), aborting `--apply`
  before writing anything.
- `restart_es()` used the wrong binary for KNULLI - it ships
  `knulli-es-swissknife`, not `batocera-es-swissknife`; falls back to `GET /quit`.
- Skip the scraper's own launcher (`PortMaster Scraper.sh`) in enumeration so it
  no longer reports itself as `unknown`.
- game-end hook: `--report` no longer shares a file with the stdout log (it was
  truncating it, so the summary toast never fired).
- Ports launcher: run `pm_finish` on the "pmscraper not found" path (was leaving
  gptokeyb running), and surface pmscraper's real exit status via `PIPESTATUS`.
- `--register-tools` now honours dry-run-by-default (only writes with `--apply`).

## v1.1.0

Coverage, reporting, auto-run, and the on-device install.

### Added
- **Coverage model.** Enumerates every `.sh`/`.squashfs` EmulationStation would
  show and classifies each into `port.json` / `catalog` / `fuzzy` / `unknown` /
  `stale`, instead of only walking installed ports. Nested launchers (inside a
  port payload dir) are flagged as folder noise.
- **Completeness detection** — `missing` / `partial` / `complete` per entry.
- **Report** at the end of every run, plus `--report FILE` (Markdown) and
  `--csv`. Exit code `1` when any `unknown` entry is present.
- **Fuzzy matching** of `.sh` stems against upstream titles (its own bucket);
  `--no-fuzzy` demotes those to `unknown`.
- **ES reload over HTTP** — `GET 127.0.0.1:1234/reloadgames` runs by default
  after `--apply` so art appears without a restart. `--no-reload` opts out;
  `--restart-es` is the fallback. The game-end hook posts a `/notify` toast.
- New flags: `--only-missing`, `--stub-unknown`, `--prune`, `--no-fuzzy`,
  `--prefer-covers`, `--no-reload`, `--report`, `--csv`.
- `--progress`: posts a per-port `/notify` toast `name [x/y]` with an ASCII bar
  while scraping (the auto-run hook enables it). Visible only while ES is in the
  foreground - the ES scraper's native progress widget isn't reachable over the
  HTTP API, so this is the closest external equivalent.
- New gamelist fields: `tags` (raw genres, for ES filtering) and `titleshot`
  (screenshot alias).
- `install.sh` (preflight, exFAT-aware permissions, verify, `--uninstall`,
  `--dry-run`), a Ports-menu launcher built on the standard PortMaster port
  skeleton, and an auto-run hook pair.
- **Auto-run hooks.** Verified against KNULLI's EmulationStation fork
  (`knulli-cfw/batocera-emulationstation`, branch `knulli`): `game-end` fires
  with *no arguments*, so a lone game-end hook can't tell what ran. A
  `game-start` hook (which receives `rom, basename, name`) drops a marker when
  PortMaster launches; the `game-end` hook consumes it and scrapes. Auto-run
  needs the exec bit, so it works on ext4 (KNULLI's SD default) but not on
  exFAT/NTFS userdata — install.sh warns, and the Ports-menu launcher still
  works there.
- Synthetic-tree test suite under `tests/`.

### Fixed
- `--prune` no longer treats deliberately-skipped-but-present launchers
  (`PortMaster.sh` above all) as stale: staleness is keyed on actual file
  existence, not the filtered ES enumeration.

## v1.0.0

Initial offline core: directory discovery (incl. card-reader partition-root
detection), installed-port scan, artwork cache indexing, idempotent
non-destructive gamelist merge with `.bak`/`.broken` safety, and `--online`
catalog fill-in.
