# Changelog

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
