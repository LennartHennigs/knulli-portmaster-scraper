# knulli-portmaster-scraper

Give your PortMaster ports proper artwork, descriptions and genres in KNULLI's
(and Batocera's) Ports menu — without scraping anything from the internet.

## The problem

Games installed through PortMaster arrive with no metadata — in the Ports menu
they're bare `.sh` filenames with no title, description or artwork. Ordinary
scrapers can't fix it (they look games up by name/hash, and a port's `.sh`
matches nothing). But PortMaster already *has* all that information on your SD
card. So this tool takes it from there and fills it in.

It looks up each installed game's info from PortMaster (its `port.json` and
artwork cache) and writes it into `/userdata/roms/ports/gamelist.xml`. Nothing
is scraped from the internet. Stdlib-only Python 3.7+, one file, no dependencies.

## Details

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

## Install

`install.sh` runs **on the device** (it writes to `/userdata/...`, detects the
filesystem, and verifies against your real ports), so you install over SSH.

1. **Enable SSH** on the handheld — KNULLI: *Main Menu → Network Settings →
   Enable SSH*. Note the device's IP; the default login is `root` / `linux`.
2. **Copy this folder to the device** — either over SSH from your computer:
   ```sh
   scp -r knulli-portmaster-scraper root@<device-ip>:/userdata/
   ```
   …or with a card reader: drop the folder anywhere on the SD card *except*
   `roms/ports/` (so ES doesn't list its `.sh` files), then reinsert it.
3. **Run the installer** over SSH:
   ```sh
   ssh root@<device-ip>
   cd /userdata/knulli-portmaster-scraper   # wherever you put it
   ./install.sh
   ```

It locates your ports + PortMaster dirs, installs four files (the tool, the
Ports-menu launcher, and a `game-start`/`game-end` hook pair), registers the
tool entries, and ends with a dry run proving it can see your ports. Re-run any
time to upgrade. `./install.sh --uninstall` removes everything (including the
scraper's own gamelist entry); `./install.sh --dry-run` shows the plan only.

> **exFAT/NTFS:** the auto-run hooks need the Unix exec bit, which exFAT/NTFS
> can't provide, so ES won't run them there (KNULLI's ext4 SD is fine). The
> **PortMaster Scraper** Ports-menu entry always works — run it by hand.

If the auto-run doesn't fire on ext4, make sure the hooks are executable (SSH):

```sh
chmod 755 /userdata/system/configs/emulationstation/scripts/game-start/pmscraper-trigger.sh \
          /userdata/system/configs/emulationstation/scripts/game-end/pmscraper-hook.sh
```

## How it runs

Two ways, both installed by `install.sh`:

1. **Automatically** — a `game-end` hook fires when you exit PortMaster and
   scrapes just the ports you installed that session (nothing else). This is the
   everyday path; you never have to think about it.
2. **By hand** — the **PortMaster Scraper** entry in the Ports menu does a full
   rescan of every installed port, with an on-screen progress bar. Use it for a
   first run, after a big cleanup, or to fetch cover art over WiFi (`--online`).

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
