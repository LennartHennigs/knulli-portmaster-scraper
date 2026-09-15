# Changelog

## v1.3.0

Better data, sourced from what's already on the card: porter-authored
`gameinfo.xml` text, and cover art ports ship with themselves. Plus a way to
force a full rewrite from the device.

### Added
- **`gameinfo.xml` preferred over `port.json` for editorial text** — when a
  port ships one (most do), its real description, developer, publisher,
  release date, genre, rating and player count now win over `port.json`'s
  short install blurb and porter-credit-as-developer. Non-destructive merge
  still applies: already-scraped ports need `--force` (or the new "Rescan
  All" entry below) to pick up the improvement.
- **Cover art resolved from the port's own directory, fully offline** — most
  ports ship a `cover.*` file directly alongside `port.json`; pmscraper now
  checks there before falling back to `--online`. Fixes box art for ports
  whose `port.json` doesn't declare a cover at all (`descent`, `descent2`,
  `doom3`, `masseffect`), which `--online` couldn't find either.
- **"PortMaster Scraper (Rescan All)" Ports-menu entry** — a second launcher
  that runs with `--force`, so you can force a full rewrite of every
  installed port's data straight from the device, no SSH needed. The
  existing "PortMaster Scraper" entry is unchanged (fills gaps only).

## v1.2.1

Visible version + a success toast you can actually see.

### Added
- **Version on screen** — the Ports launcher shows `PortMaster Scraper v<x.y.z>`
  above the progress bar and in the final summary; the auto-run toast and the
  installer's toasts carry it too.

### Fixed
- **Installer success toast never appeared** — it was posted while ES was
  backgrounded (the installer runs as a Ports entry) and then ES was restarted,
  so the install looked like a bare reboot. The toast is now handed to a
  detached waiter that fires once ES's HTTP server answers again.
- **`install.sh` filesystem check printed garbage on macOS** — `stat -f -c %T`
  is GNU syntax; BSD/macOS `stat -f` means something else. Falls back to
  `df` + `mount` there, so installing from a Mac card reader reports the real
  filesystem.

## v1.2.0

On-device polish: graphical progress, smarter auto-run, release hardening.

### Added
- **On-screen progress bar** — the Ports launcher drives PortMaster's GUI
  (`pugwash`) over its dialog FIFO; `pmscraper --emit-progress` feeds it.
- **`--since EPOCH`** — auto-run scrapes only newly-installed ports, not a full
  rescan (game-start records the launch time, game-end passes it).
- **`--register-tools`** — tidy gamelist entries for PortMaster + the Scraper
  instead of bare filenames; run by `install.sh`.
- The Ports launcher runs `--online` (covers aren't cached locally); degrades
  cleanly offline.
- Exit code **2** for errors (crash or bad config), distinct from `1`
  (unidentified `.sh` present).
- `install.sh --uninstall` removes the scraper's own gamelist entry.
- **No-SSH install** — `make-release.sh` builds a self-extracting
  `Install PortMaster Scraper.sh`; drop it in `roms/ports/`, run it from the Ports
  menu, and it installs everything on-device and removes itself.

### Changed / Fixed
- `--progress` toast dropped the ASCII bar (renders badly in a popup).
- Crash on ports whose `attr.image` is a bare string (e.g. `descent`).
- `restart_es()` uses `knulli-es-swissknife` (not `batocera-es-swissknife`),
  falling back to `GET /quit`.
- Skip the scraper's own launcher in enumeration (was reporting itself
  `unknown`).
- game-end hook: `--report` no longer shares a file with the log (it truncated
  it, so the toast never fired).
- Ports launcher: `pm_finish` on the not-found path; real exit status via
  `PIPESTATUS`.
- `--register-tools` honours dry-run-by-default.
- Summary counts only what a run actually resolved — `--since`/`--only-missing`
  runs no longer over-report "scraped".
- Enumeration prunes the `PortMaster/` dir (ArkOS/JELOS layouts) so its internal
  `.sh` don't show as `unknown`.
- game-end hook falls back to `--only-missing` if the marker isn't a number.

## v1.1.0

Coverage model, reporting, auto-run, on-device install.

### Added
- Enumerate every `.sh`/`.squashfs` ES shows and classify into
  `port.json` / `catalog` / `fuzzy` / `unknown` / `stale`, with
  `missing`/`partial`/`complete` completeness.
- Report each run; `--report FILE` (Markdown) / `--csv`. Exit `1` on any
  `unknown`.
- ES reload over HTTP (`GET /reloadgames`) after `--apply`; `--restart-es`
  fallback.
- New gamelist fields `tags` + `titleshot`; new flags `--only-missing`,
  `--stub-unknown`, `--prune`, `--no-fuzzy`, `--prefer-covers`, `--progress`,
  `--report`/`--csv`, `--no-reload`.
- `install.sh` + Ports-menu launcher + `game-start`/`game-end` auto-run hooks
  (the marker pattern — `game-end` fires with no args; needs the exec bit, so
  ext4 only). Synthetic-tree test suite.

### Fixed
- `--prune` keys staleness on real file existence, so it never deletes a
  present-but-skipped launcher (`PortMaster.sh`).

## v1.0.0

Offline core: directory discovery (incl. card-reader detection), installed-port
scan, artwork-cache indexing, idempotent non-destructive gamelist merge with
`.bak`/`.broken` safety, and `--online` catalog fill-in.
