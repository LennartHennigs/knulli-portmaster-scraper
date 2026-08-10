# knulli-portmaster-scraper

Give your PortMaster ports proper artwork, descriptions and genres in KNULLI's
(and Batocera's) Ports menu — without scraping anything from the internet.

## The problem

PortMaster ports show up as bare `.sh` filenames: no art, no description. The
usual scrapers can't help — they index by game name/hash, and `Sonic 3 AIR.sh`
matches nothing. But the metadata is already **on your SD card**: PortMaster
writes a `port.json` per port and caches every port's artwork. This tool just
*transcribes* that into `/userdata/roms/ports/gamelist.xml`.

Stdlib-only Python 3.7+, one file. No dependencies, no API keys.

## What it does

- Writes `name`, `desc`, `genre`, `tags`, `developer`, `publisher`, `rating`,
  `image`, `thumbnail`, `titleshot` from `port.json` + the `images_pm/` cache.
- **Non-destructive & idempotent** — only fills fields it owns, never touches
  `favorite`/`playcount`/etc., backs up to `.bak`, writes atomically; a second
  run is a no-op. Dry run is the default — only `--apply` writes.
- **Reloads ES in place** (its local HTTP API) — art appears without a restart.
- **Reports what it can't identify** — non-PortMaster `.sh` are never overwritten.
- **`--online`** fills cover/box art (not in the local cache) from the PortMaster
  repo; degrades cleanly offline.
- **Auto-runs** after you exit PortMaster, scraping just the ports you installed.

## Install (on the device)

```sh
./install.sh              # locates dirs, installs 4 files, ends with a dry run
./install.sh --uninstall  # removes them + the scraper's own gamelist entry
./install.sh --dry-run    # show the plan, change nothing
```

> **exFAT/NTFS:** the auto-run hooks need the Unix exec bit, which exFAT/NTFS
> can't provide, so ES won't run them there (KNULLI's ext4 SD is fine). The
> **PortMaster Scraper** Ports-menu entry always works — run it by hand.

## Use

From the handheld: **Ports → PortMaster Scraper** (shows a progress bar). Over
SSH or a card reader:

```sh
python3 pmscraper.py                                # dry run - shows the plan
python3 pmscraper.py --apply --online               # write it, fetch covers
python3 pmscraper.py --ports-dir /Volumes/SD/roms/ports   # from a card reader
```

### Useful flags

| Flag | Effect |
|---|---|
| `--apply` | actually write (default is a dry run) |
| `--online` | fetch cover art + `ports.json` from GitHub |
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
