# knulli-portmaster-scraper

Give your PortMaster ports proper artwork, descriptions and genres in KNULLI's
(and Batocera's) Ports menu — without scraping anything from the internet.

## The problem

Ports installed through PortMaster show up in the Ports menu as bare `.sh`
filenames: no box art, no screenshot, no description. The usual scrapers
(ScreenScraper, TheGamesDB, Skraper) can't help — they index games by
name/hash, and `Sonic 3 AIR.sh` matches nothing in their databases.

But the metadata already exists **on your SD card**. PortMaster writes a
`port.json` for every port it installs, and it has already downloaded every
port's screenshot and cover art into its own cache. Nothing needs to be
scraped — it just needs to be *transcribed* into
`/userdata/roms/ports/gamelist.xml`.

That's all this tool does.

## What it does

- Reads each installed port's `port.json` and PortMaster's artwork cache
  (`images_pm/`), and writes `name`, `desc`, `genre`, `tags`, `developer`,
  `publisher`, `rating`, `image`, `thumbnail` and `titleshot` into the ports
  gamelist.
- **Non-destructive.** It only fills fields it owns and never overwrites values
  you (or ES) already set unless you pass `--force`. `favorite`, `playcount`,
  `lastplayed`, `hidden` are never touched. It backs up to `gamelist.xml.bak`
  and writes atomically.
- **Idempotent.** Run it as often as you like; a second run is a no-op.
- **Reloads ES in place** via its local HTTP API — no restart, art appears
  immediately.
- **Tells you what it couldn't identify.** Non-PortMaster `.sh` files are
  reported, never silently overwritten.
- Optional `--online` mode fills gaps (hand-installed ports with no `port.json`,
  or a missing artwork cache) from the PortMaster catalog.
- **Runs automatically** after you exit PortMaster, so freshly installed ports
  are scraped without you lifting a finger.

Stdlib-only Python 3.7+, one file. No dependencies, no API keys.

## Install (on the device)

Copy this folder to the device (or run it from a mounted card) and:

```sh
./install.sh
```

It locates your ports and PortMaster dirs, installs four files (the tool, the
Ports-menu launcher, and a `game-start`/`game-end` hook pair), and ends with a
dry run proving it can see your ports. Re-run it any time to upgrade.
`./install.sh --uninstall` removes it (leaving your gamelist and art alone);
`./install.sh --dry-run` shows the plan without changing anything.

> **exFAT/NTFS note:** the auto-run hooks need the Unix exec bit, which exFAT
> and NTFS can't provide — on those cards ES silently won't run them. KNULLI's
> default ext4 SD layout is unaffected. Either way, the **PortMaster Scraper**
> entry in the Ports menu always works; run it there by hand.

## Use

From the handheld: **Ports → PortMaster Scraper**.

Over SSH, or from a card reader on your computer:

```sh
python3 pmscraper.py                                  # dry run - shows the plan
python3 pmscraper.py --apply                          # write it
python3 pmscraper.py --apply --online                 # also fill gaps online
python3 pmscraper.py --ports-dir /Volumes/SD/roms/ports   # from a card reader
```

The default is always a **dry run** — it opens files read-only, writes nothing,
and prints a full report. Only `--apply` ever writes.

### Useful flags

| Flag | Effect |
|---|---|
| `--apply` | actually write (default is a dry run) |
| `--online` | fetch `ports.json` + missing artwork from GitHub |
| `--force` | overwrite values already in the gamelist |
| `--only-missing` | only touch entries that are missing/partial |
| `--since EPOCH` | only scrape ports whose `port.json` is newer (the auto-run's "new ports only") |
| `--register-tools` | give PortMaster + the Scraper tidy Ports-menu entries (needs `--apply`) |
| `--prefer-covers` | use cover art as `<image>` (for themes without `<thumbnail>`) |
| `--stub-unknown` | give non-PortMaster `.sh` a tidied `<name>` and nothing else |
| `--prune` | remove gamelist entries whose `.sh` is gone |
| `--no-fuzzy` | treat title-only catalog matches as unknown |
| `--port-dates` | use the port's release date as `<releasedate>` |
| `--progress` | per-port ES toast `name [x/y]` while scraping (visible only while ES is foreground — the auto-run hook or an SSH run) |
| `--report FILE` / `--csv` | write the full classification breakdown |
| `--no-reload` / `--restart-es` | control how ES refreshes afterward |

Exit code is `0` when everything resolved, `1` when there were unidentified
`.sh` files — so the auto-run hook knows whether to flag them.

## How it decides what to scrape

It enumerates every `.sh`/`.squashfs` EmulationStation would show, then sorts
each into a bucket:

| Bucket | Meaning | Action |
|---|---|---|
| `port.json` | PortMaster-installed, metadata on disk | scraped (offline) |
| `catalog` | no `port.json`, but the `.sh` matches an upstream archive name | scraped (needs `--online`) |
| `fuzzy` | matches an upstream *title* after normalisation | scraped, but listed to confirm |
| `unknown` | matches nothing — not a PortMaster port | never touched; reported |
| `stale` | a gamelist entry whose `.sh` is gone | reported; removed only with `--prune` |

## Development

```sh
python3 -m unittest discover -s tests
```

The tests build a throwaway synthetic KNULLI tree and exercise the whole flow
(no network, no device).

## License

MIT.
