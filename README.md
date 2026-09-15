# knulli-portmaster-scraper

Give your PortMaster ports proper titles, descriptions, genres and artwork in
KNULLI's (and Batocera's) Ports menu — from the data PortMaster already put on
the card.

- Author: Lennart Hennigs (<https://www.lennarthennigs.de>)
- Copyright (C) 2026 Lennart Hennigs.
- Released under the MIT license.

To see the latest changes please take a look at the
[Changelog](https://github.com/LennartHennigs/knulli-portmaster-scraper/blob/main/CHANGELOG.md).

If you find this tool helpful please consider giving it a ⭐️ at
[GitHub](https://github.com/LennartHennigs/knulli-portmaster-scraper) and/or
[buy me a ☕️](https://ko-fi.com/lennart0815).

Thank you!

## Description

Games installed through PortMaster arrive with no metadata — in the Ports menu
they're bare `.sh` filenames with no title, description or artwork. Ordinary
scrapers can't fix it (they look games up by name/hash, and a port's `.sh`
matches nothing). But PortMaster already *has* all that information on your SD
card: it wrote a `port.json` for every port it installed and downloaded the
artwork alongside it. So this tool takes it from there and fills it in.

It reads each installed game's info from PortMaster — its `port.json`, a
porter-authored `gameinfo.xml` when the port ships one (real description,
developer, publisher, release date — better than `port.json`'s short install
blurb), and artwork already on the card (including cover art most ports ship
in their own folder) — and writes it into `/userdata/roms/ports/gamelist.xml`,
the file EmulationStation reads to draw the Ports menu. No scraper service,
and no internet needed for most of it; `--online` only tops up the covers
that aren't on the card already. The merge is non-destructive and idempotent:
a second run is a no-op, and anything you (or ES's own scraper) already set
is left alone.

It's a single stdlib-only Python file — no dependencies — and it runs both on the
handheld and from a card reader on your computer. Tested on an Anbernic RG40XXH
running KNULLI; it should work on any KNULLI/Batocera device with PortMaster and
Python 3.7+.

## Details

- **It prefills:** `name`, `desc`, `genre`, `tags`, `developer`, `publisher`,
  `releasedate`, `rating`, `players`, `image`, `thumbnail`, `titleshot` — from
  `gameinfo.xml` (when present) + `port.json` + artwork already on the card
  (a port's own cover file, or the `images_pm/` cache).
- **Non-destructive & idempotent** — only fills fields it owns, never touches
  `favorite`/`playcount`/etc., backs up to `.bak`, writes atomically; a second
  run is a no-op. Dry run is the default — only `--apply` writes.
- **Reloads ES in place** (its local HTTP API) — art appears without a restart.
- **Reports what it can't identify** — non-PortMaster `.sh` are never overwritten.
- **`--online`** fills cover/box art still missing after checking the card,
  from the PortMaster repo; degrades cleanly offline.
- **Auto-runs** after you exit PortMaster, scraping just the ports you installed.

## Install

**No SSH, no computer terminal** — one file, run from the Ports menu:

1. Download `knulli-portmaster-scraper-vX.Y.Z.zip` from the
   [Releases](https://github.com/LennartHennigs/knulli-portmaster-scraper/releases) page and unzip it.
2. Copy the single **`Install PortMaster Scraper.sh`** onto your SD card's
   `roms/ports/` folder — via a card reader, or over KNULLI's network share
   (`\\<device-ip>\share` → `roms/ports`).
3. On the handheld: **Ports → Install PortMaster Scraper**. It installs
   everything and restarts EmulationStation, then removes itself.

Done — **PortMaster Scraper** and **PortMaster Scraper (Rescan All)** entries
appear in the Ports menu, and new ports are scraped automatically when you
exit PortMaster. Run the installer again any time to upgrade.

> **exFAT/NTFS:** the auto-run hooks need the Unix exec bit, which exFAT/NTFS
> can't provide, so ES won't run them there (KNULLI's ext4 SD is fine). The
> **PortMaster Scraper** menu entry always works — run it by hand.

<details>
<summary><b>Advanced: install / uninstall over SSH</b></summary>

Instead of the packaged installer, run `install.sh` from the repo on the device:

```sh
scp -r knulli-portmaster-scraper root@<device-ip>:/userdata/   # SSH must be on
ssh root@<device-ip>
cd /userdata/knulli-portmaster-scraper
./install.sh              # detect dirs, install 5 files, register, verify
./install.sh --uninstall  # remove them + the scraper's gamelist entry
./install.sh --dry-run    # show the plan only
```

If the auto-run doesn't fire on ext4, make sure the hooks are executable:

```sh
chmod 755 /userdata/system/configs/emulationstation/scripts/game-start/pmscraper-trigger.sh \
          /userdata/system/configs/emulationstation/scripts/game-end/pmscraper-hook.sh
```

Build the release artifact yourself with `./make-release.sh` (writes to `build/`).
</details>

## How it runs

Three ways, all installed by `install.sh`:

1. **By hand, gaps only** — the **PortMaster Scraper** entry in the Ports menu
   scans every installed port and fills in whatever's missing, with an
   on-screen progress bar. Use it for a first run, after a big cleanup, or to
   fetch remaining cover art over WiFi (`--online`). Never overwrites a field
   that already has a value.
2. **By hand, force a full rewrite** — **PortMaster Scraper (Rescan All)**
   does the same scan but with `--force`, overwriting every field with
   freshly-resolved data. Use it after updating pmscraper, or if a port's
   metadata looks stale from an older run.
3. **Automatically** — a `game-end` hook fires when you exit PortMaster and
   scrapes just the ports you installed that session (nothing else). This is the
   everyday path; you never have to think about it.

You can also run `pmscraper.py` directly over SSH or from a card reader:

```sh
python3 pmscraper.py                                # dry run - shows the plan
python3 pmscraper.py --apply --online               # write it, fetch covers
python3 pmscraper.py --ports-dir /Volumes/SD/roms/ports   # from a card reader
```

### Useful flags

| Flag | Effect |
|---|---|
| `--apply` | actually write (default is a dry run) |
| `--online` | fetch cover art still missing after checking the card, + `ports.json` from GitHub |
| `--force` | overwrite values already in the gamelist |
| `--only-missing` / `--since EPOCH` | limit to missing/partial, or to newly-installed ports |
| `--prefer-covers` | use cover art as `<image>` (themes without `<thumbnail>`) |
| `--stub-unknown` / `--prune` | tidy-name unknown `.sh` / drop entries whose `.sh` is gone |
| `--progress` | per-port ES toast `name [m/n]` (foreground only) |
| `--report FILE` / `--csv` | write the full classification breakdown |
| `--no-fuzzy` `--port-dates` `--no-reload` `--restart-es` | matching / dates / ES refresh |

Exit code: `0` clean, `1` unidentified `.sh` present, `2` error.

## How it decides what to scrape

Enumerates every `.sh`/`.squashfs` ES would show and sorts each into a bucket:

| Bucket | Meaning | Action |
|---|---|---|
| `port.json` | PortMaster-installed, metadata on disk | scraped (offline) |
| `catalog` / `fuzzy` | `.sh` matches an upstream archive name / title | scraped (needs `--online`) |
| `unknown` | not a PortMaster port | never touched; reported |
| `stale` | gamelist entry whose `.sh` is gone | reported; removed only with `--prune` |

## Development

```sh
python3 -m unittest discover -s tests   # throwaway synthetic tree, no network/device
```

## License

MIT.
