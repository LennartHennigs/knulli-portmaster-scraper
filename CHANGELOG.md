# Changelog

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
