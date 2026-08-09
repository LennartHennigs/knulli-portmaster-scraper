#!/usr/bin/env python3
"""
pmscraper - a PortMaster "scraper" for KNULLI / Batocera-based firmware.

Instead of asking ScreenScraper/TheGamesDB about .sh launcher scripts (which they
do not know about), this reads the metadata PortMaster already put on the SD card
and writes it straight into /userdata/roms/ports/gamelist.xml.

Sources of truth, in order:
  1. <ports>/<portdir>/port.json          - written by PortMaster on install
  2. <tools>/PortMaster/config/images_pm/ - artwork cache (images.zip)
  3. --online: ports.json + screenshots from the PortMaster GitHub release

Usage:
    python3 pmscraper.py                 # dry run, prints what it would do
    python3 pmscraper.py --apply
    python3 pmscraper.py --apply --online     # fill gaps from the internet
    python3 pmscraper.py --apply --force      # overwrite existing gamelist values

After --apply the tool reloads EmulationStation over its local HTTP API so the
Ports menu refreshes without a restart (disable with --no-reload).
"""

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
# stdlib ElementTree by design: KNULLI ships a bare Python with no defusedxml,
# and this only ever parses gamelist.xml files the device itself wrote, on the
# same SD card - not untrusted network input. If that ever changes, switch to
# defusedxml.ElementTree.
import xml.etree.ElementTree as ET

from pathlib import Path

VERSION = "1.1.0"

PORTS_JSON_URL = "https://github.com/PortsMaster/PortMaster-New/releases/latest/download/ports.json"
RAW_PORT_URL = "https://raw.githubusercontent.com/PortsMaster/PortMaster-New/main/ports/{port}/{file}"

# EmulationStation's local HTTP API (always on in KNULLI); /reloadgames re-reads
# gamelists from disk - the same path as the menu's "Update Gamelists".
ES_HOST = "127.0.0.1"
ES_PORT = 1234

# Candidate roms/ports directories, most specific first.
PORTS_DIR_CANDIDATES = (
    "/userdata/roms/ports",      # KNULLI / Batocera / REG-Linux
    "/roms/ports",               # ArkOS / TheRA / AmberELEC-likes
    "/storage/roms/ports",       # JELOS / ROCKNIX
    "/roms2/ports",
)

# Candidate PortMaster config directories (holds images_pm/).
CFG_DIR_CANDIDATES = (
    "{xdg}/PortMaster/config",
    "/userdata/system/.local/share/PortMaster/config",
    "{ports}/PortMaster/config",
    "/roms/ports/PortMaster/config",
    "/storage/roms/ports/PortMaster/config",
)

# gamelist.xml fields this tool owns. Anything else in an existing entry
# (favorite, playcount, lastplayed, hidden, ...) is left untouched.
MANAGED_TAGS = (
    "name", "desc", "image", "thumbnail", "titleshot", "genre", "tags",
    "developer", "publisher", "releasedate", "rating",
)

# Top-level launchers that are tools, not scrapeable games - always skipped so
# they don't show up as "unknown": PortMaster itself, and our own Ports entry.
SKIP_LAUNCHERS = frozenset(("PortMaster.sh", "PortMaster Scraper.sh"))

# ES game extensions for the ports system (es_systems.yml: [sh, squashfs]).
ES_EXTENSIONS = (".sh", ".squashfs")

GENRE_FIXUPS = {
    "fps": "FPS",
    "rpg": "RPG",
    "casino/card": "Casino/Card",
    "visual novel": "Visual Novel",
}

# The two artwork kinds, mapped to their gamelist media-file suffix. Single
# source of truth for "which art kinds exist" - iterated by resolve_art and the
# online download; the gamelist slot each maps to lives in build_fields.
ART_DEST_SUFFIX = {"screenshot": "image", "cover": "thumb"}


def log(msg=""):
    print(msg, flush=True)


def name_cleaner(text):
    """Mirrors harbourmaster.util.name_cleaner so image lookups line up."""
    temp = re.sub(r"[^a-zA-Z0-9 _\-\.]+", "", text.strip().lower())
    return re.sub(r"[ \.]+", ".", temp)


def strip_zip(name):
    """Drop a trailing .zip from a PortMaster archive name."""
    return name[:-4] if name.lower().endswith(".zip") else name


def tidy_name(stem):
    """Turn a bare .sh stem into a human-ish title for --stub-unknown."""
    words = re.split(r"[ _\-]+", stem.strip())
    return " ".join(w[:1].upper() + w[1:] if w else w for w in words).strip()


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def find_ports_dir(override=None):
    if override:
        p = Path(override)
        if not p.is_dir():
            sys.exit(f"error: --ports-dir {p} is not a directory")
        return p

    env = os.environ.get("HM_PORTS_DIR")
    if env and Path(env).is_dir():
        return Path(env)

    for cand in PORTS_DIR_CANDIDATES:
        p = Path(cand)
        if p.is_dir():
            return p

    sys.exit("error: could not find a ports directory; pass --ports-dir")


def find_cfg_dir(ports_dir, override=None):
    if override:
        p = Path(override)
        return p if p.is_dir() else None

    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")

    # When ports_dir looks like <root>/roms/ports (a card read from a reader),
    # derive the partition root and look for PortMaster's config beneath it.
    extra = []
    parts = ports_dir.resolve().parts
    if len(parts) >= 2 and parts[-2:] == ("roms", "ports"):
        root = Path(*parts[:-2])
        extra.append(root / "system" / ".local" / "share" / "PortMaster" / "config")

    for tmpl in CFG_DIR_CANDIDATES:
        extra.append(Path(tmpl.format(xdg=xdg, ports=ports_dir)))

    for p in extra:
        if (p / "images_pm").is_dir() or (p / "config.json").is_file():
            return p

    # Last resort: hunt for it.
    search_roots = [Path(xdg), ports_dir, Path("/userdata"), Path("/roms")]
    if len(parts) >= 2 and parts[-2:] == ("roms", "ports"):
        search_roots.insert(0, Path(*parts[:-2]))
    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            for hit in root.glob("**/PortMaster/config/images_pm"):
                return hit.parent
        except OSError:
            pass

    return None


def index_images(cfg_dir):
    """images_pm/<cleanname>.<type>.<ext> -> {cleanname: {type: Path}}"""
    index = {}
    if cfg_dir is None:
        return index

    for images_dir in sorted(cfg_dir.glob("images_*")):
        if not images_dir.is_dir():
            continue
        for f in images_dir.iterdir():
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            if f.name.count(".") < 2:
                continue
            stem, kind, _ext = f.name.lower().rsplit(".", 2)
            index.setdefault(name_cleaner(stem), {}).setdefault(kind, f)

    return index


def enumerate_es_entries(ports_dir):
    """Every .sh/.squashfs EmulationStation would show, the way ES finds them.

    Mirrors SystemData::populateFolder: recurse into subfolders, skip dot-files
    and dot-folders. Returns {relpath: nested_bool}. `nested` marks a launcher
    inside a port's own payload directory (ES shows it, usually as folder noise).
    """
    entries = {}
    for dirpath, dirnames, filenames in os.walk(ports_dir):
        rel_dir = Path(dirpath).relative_to(ports_dir)
        # Prune hidden dirs and PortMaster's own tooling/config trees.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        depth = 0 if rel_dir == Path(".") else len(rel_dir.parts)
        for fn in filenames:
            if fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() not in ES_EXTENSIONS:
                continue
            if depth == 0 and fn in SKIP_LAUNCHERS:
                continue
            rel = fn if depth == 0 else str(rel_dir / fn)
            entries[normalise_path(rel)] = depth > 0
    return entries


# --------------------------------------------------------------------------- #
# Installed ports
# --------------------------------------------------------------------------- #

def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out = []
        for v in value.values():
            out.extend(_as_list(v))
        return out
    return list(value)


def scan_installed_ports(ports_dir):
    """Every <ports>/<dir>/port.json PortMaster wrote at install time."""
    port_files = sorted(ports_dir.glob("*/port.json")) + sorted(ports_dir.glob("*/*.port.json"))

    seen_dirs = set()
    ports = []

    for pf in port_files:
        if pf.parent in seen_dirs:
            continue
        seen_dirs.add(pf.parent)

        try:
            with pf.open("r", encoding="utf-8") as fh:
                info = json.load(fh)
        except (OSError, ValueError) as err:
            log(f"  ! skipping {pf}: {err}")
            continue

        if not isinstance(info, dict) or "attr" not in info:
            continue

        scripts = resolve_scripts(ports_dir, info)
        if not scripts:
            # A port with no launcher .sh in the ports root (background service
            # like librespot, or a data-only pack). Nothing for ES to show.
            continue

        ports.append((info, scripts))

    return ports


def resolve_scripts(ports_dir, info):
    """Which .sh launchers in the ports root belong to this port."""
    candidates = []
    candidates.extend(_as_list(info.get("items")))
    candidates.extend(_as_list(info.get("items_opt")))

    files = info.get("files")
    if isinstance(files, dict):
        candidates.extend(files.keys())
        candidates.extend(_as_list(files))

    scripts = []
    for c in candidates:
        if not isinstance(c, str) or not c.lower().endswith(".sh"):
            continue
        c = c.lstrip("./")
        if "/" in c:
            continue
        if (ports_dir / c).is_file() and c not in scripts:
            scripts.append(c)

    return scripts


def port_stem(info):
    return name_cleaner(strip_zip(info.get("name") or ""))


# --------------------------------------------------------------------------- #
# Online fill-in
# --------------------------------------------------------------------------- #

def fetch(url, binary=False, timeout=30, data=None, method=None):
    import urllib.request
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": f"pmscraper/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = resp.read()
    return out if binary else out.decode("utf-8")


def load_online_catalog(cache_file):
    if cache_file.is_file():
        try:
            with cache_file.open("r", encoding="utf-8") as fh:
                return json.load(fh).get("ports", {})
        except (OSError, ValueError):
            pass

    log(f"  downloading {PORTS_JSON_URL}")
    data = json.loads(fetch(PORTS_JSON_URL))
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with cache_file.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass

    return data.get("ports", {})


def download_art(info, kind, dest_dir):
    """Grab screenshot/cover straight from the PortMaster repo."""
    img = (info.get("attr") or {}).get("image")
    # attr.image is usually a {screenshot, covers} dict, but some ports (descent,
    # descent2) store it as a bare screenshot filename string. Normalise both.
    if isinstance(img, str):
        img = {"screenshot": img}
    elif not isinstance(img, dict):
        img = {}
    if kind == "screenshot":
        fname = img.get("screenshot")
    else:
        covers = img.get("covers") or []
        fname = covers[0] if covers else None

    if not fname:
        return None

    portdir = strip_zip(info.get("name") or "")
    url = RAW_PORT_URL.format(port=portdir, file=fname)

    try:
        blob = fetch(url, binary=True)
    except Exception as err:
        log(f"  ! {portdir}: {kind} download failed ({err})")
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{name_cleaner(portdir)}.{kind}{Path(fname).suffix.lower()}"
    out.write_bytes(blob)
    return out


# --------------------------------------------------------------------------- #
# gamelist.xml
# --------------------------------------------------------------------------- #

def build_fields(info, scripts, path, art, port_dates=False, prefer_covers=False):
    attr = info.get("attr") or {}

    if len(scripts) > 1:
        name = Path(path).stem
    else:
        name = attr.get("title") or Path(path).stem

    fields = {"name": name}

    desc = (attr.get("desc") or "").strip()
    inst = (attr.get("inst") or "").strip()
    if desc and inst and inst.lower() not in ("ready to run.", "ready to run"):
        desc = f"{desc}\n\n{inst}"
    if desc:
        fields["desc"] = desc

    genres = [g for g in (attr.get("genres") or []) if g]
    if genres:
        fields["genre"] = ", ".join(GENRE_FIXUPS.get(g, g.title()) for g in genres)
        # Raw genres for ES's tag-based filtering.
        fields["tags"] = ", ".join(genres)

    porters = [p for p in (attr.get("porter") or []) if p]
    if porters:
        fields["developer"] = ", ".join(porters)
    fields["publisher"] = "PortMaster"

    # date_added is when the *port* landed, not when the game came out, so this
    # is opt-in - it is useful for "sort by newest port", misleading otherwise.
    if port_dates:
        source = info.get("source") or {}
        added = source.get("date_added") or ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", added):
            fields["releasedate"] = added.replace("-", "") + "T000000"

    rating = info.get("rating") or {}
    avg, mx = rating.get("average_rating"), rating.get("max_rating")
    try:
        denom = float(mx or 5)
        if avg is not None and denom > 0:
            fields["rating"] = f"{max(0.0, min(1.0, float(avg) / denom)):.4f}"
    except (TypeError, ValueError):
        pass

    shot = art.get("screenshot")
    cover = art.get("cover")
    # ES stores <image> and <thumbnail> ("Box") as independent slots; fill both,
    # plus <titleshot> as a screenshot alias. --prefer-covers swaps the first two
    # for themes that only render <image>.
    primary, secondary = (cover, shot) if prefer_covers else (shot, cover)
    if primary:
        fields["image"] = primary
    if secondary:
        fields["thumbnail"] = secondary
    if shot:
        fields["titleshot"] = shot

    return fields


def normalise_path(text):
    if not text:
        return ""
    return text.strip().lstrip("./")


def load_gamelist(path):
    if path.is_file() and path.stat().st_size > 0:
        try:
            return ET.parse(str(path)).getroot()
        except ET.ParseError as err:
            log(f"  ! {path} is malformed ({err}); starting a fresh one "
                f"(old file kept as .broken)")
            shutil.copy2(str(path), str(path) + ".broken")
    return ET.Element("gameList")


def write_gamelist(root, path):
    if hasattr(ET, "indent"):
        ET.indent(root, space="  ", level=0)

    buf = io.StringIO()
    buf.write("<?xml version='1.0' encoding='utf-8'?>\n")
    buf.write(ET.tostring(root, encoding="unicode"))
    buf.write("\n")

    if path.is_file():
        shutil.copy2(str(path), str(path) + ".bak")

    tmp = path.with_suffix(".xml.tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    os.replace(str(tmp), str(path))


def tag_text(game, tag):
    node = game.find(tag)
    return (node.text or "").strip() if node is not None else ""


# --------------------------------------------------------------------------- #
# EmulationStation control
# --------------------------------------------------------------------------- #

def reload_es(timeout=5):
    """GET /reloadgames so ES re-reads the file we just wrote, instead of
    overwriting it with its in-memory copy at its next exit."""
    url = f"http://{ES_HOST}:{ES_PORT}/reloadgames"
    try:
        fetch(url, timeout=timeout)
        return True
    except Exception as err:
        log(f"  ! reload failed ({err}); use the menu's Update Gamelists, "
            f"or pass --restart-es")
        return False


def notify_es(message, timeout=2):
    """POST /notify - a short on-screen toast on the handheld (best-effort).

    Only visible while ES is in the foreground (the auto-run hook, or an SSH run
    from the menu) - not when this runs as a port launched from the Ports menu,
    since ES is then backgrounded behind the launcher."""
    try:
        fetch(f"http://{ES_HOST}:{ES_PORT}/notify",
              data=message.encode("utf-8"), method="POST", timeout=timeout)
        return True
    except Exception:
        return False


def progress_bar(done, total, width=10):
    filled = int(round(width * done / total)) if total else width
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def restart_es():
    cmd = ["batocera-es-swissknife", "--restart"]
    if shutil.which(cmd[0]):
        log(f"running: {' '.join(cmd)}")
        subprocess.run(cmd, check=False)
        return True
    log("  ! could not find batocera-es-swissknife to restart ES")
    return False


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

class Entry:
    """One thing ES would show, plus what pmscraper worked out about it."""

    __slots__ = ("path", "nested", "bucket", "info", "scripts",
                 "state", "fields", "note")

    def __init__(self, path, nested=False):
        self.path = path          # normalised, e.g. "Balatro.sh" - also the
                                  # .sh used for naming + art (one entry = one .sh)
        self.nested = nested
        self.bucket = "unknown"   # port.json | catalog | fuzzy | unknown | stale
        self.info = None          # port.json / catalog dict for the source port
        self.scripts = None       # sibling launchers (multi-launcher naming)
        self.state = None         # missing | partial | complete
        self.fields = None        # built gamelist fields
        self.note = ""            # human hint for the report

    @property
    def identifiable(self):
        return self.bucket in ("port.json", "catalog", "fuzzy")


def classify(es_entries, installed, catalog, use_fuzzy):
    """Turn the raw ES entry set into classified Entry objects."""
    # script basename -> (info, scripts)
    by_script = {}
    for info, scripts in installed:
        for s in scripts:
            by_script[normalise_path(s)] = (info, scripts)

    # catalog lookup tables
    by_zip = {}
    by_title = {}
    for key, cat in (catalog or {}).items():
        by_zip[name_cleaner(strip_zip(key))] = cat
        title = (cat.get("attr") or {}).get("title") or ""
        if title:
            by_title.setdefault(name_cleaner(title), cat)

    entries = []
    for path, nested in sorted(es_entries.items()):
        e = Entry(path, nested)
        stem = name_cleaner(Path(path).stem)

        if path in by_script:
            e.bucket = "port.json"
            e.info, e.scripts = by_script[path]
        elif not nested and stem in by_zip:
            e.bucket = "catalog"
            e.info, e.scripts = by_zip[stem], [path]
        elif not nested and use_fuzzy and stem in by_title:
            e.bucket = "fuzzy"
            e.info, e.scripts = by_title[stem], [path]
            e.note = "matched upstream title, not archive name - confirm"
        else:
            e.bucket = "unknown"
            e.note = "nested - ES shows this inside a folder" if nested else ""

        entries.append(e)

    return entries


def find_stale(by_path, ports_dir):
    """gamelist entries whose file no longer exists on disk.

    Keyed on actual file existence, not our (filtered) ES enumeration: entries
    we deliberately skip enumerating - PortMaster.sh above all - still exist on
    disk and are emphatically not stale, so --prune must never touch them.
    Reuses the by_path index rather than re-walking <game> nodes.
    """
    stale = []
    for key, game in by_path.items():
        if key and not (ports_dir / key).exists():
            e = Entry(key)
            e.bucket = "stale"
            name = tag_text(game, "name")
            e.note = f'"{name}"' if name else ""
            stale.append(e)
    return stale


def resolve_art(e, images, media_dir, ports_dir, cfg_dir, apply, online):
    """Locate an entry's artwork, copy it into media_dir (under --apply), and
    return {kind: gamelist-relative-path}. The single place that knows how art
    files are named on disk."""
    art_src = dict(images.get(port_stem(e.info), {}))

    if online and apply and cfg_dir is not None:
        for kind in ART_DEST_SUFFIX:
            if kind not in art_src:
                got = download_art(e.info, kind, cfg_dir / "images_pm")
                if got:
                    art_src[kind] = got

    art = {}
    for kind, suffix in ART_DEST_SUFFIX.items():
        src = art_src.get(kind)
        if src is None:
            continue
        dest = media_dir / f"{Path(e.path).stem}-{suffix}{src.suffix.lower()}"
        if apply:
            media_dir.mkdir(parents=True, exist_ok=True)
            if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
                shutil.copy2(str(src), str(dest))
        art[kind] = "./" + str(dest.relative_to(ports_dir))
    return art


def completeness(game):
    """missing | partial | complete for an existing gamelist node."""
    if game is None:
        return "missing"
    if tag_text(game, "image") and tag_text(game, "desc"):
        return "complete"
    return "partial"


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def print_summary(entries, stale):
    src = {"port.json": 0, "catalog": 0, "fuzzy": 0}
    scraped = skipped = 0
    for e in entries:
        if not e.identifiable:
            continue
        if e.state == "complete":
            skipped += 1
        else:
            scraped += 1
            src[e.bucket] += 1

    unknown = [e for e in entries if e.bucket == "unknown"]

    log()
    log(f"scraped  {scraped:3d}  ({src['port.json']} from port.json, "
        f"{src['catalog']} from catalog, {src['fuzzy']} fuzzy)")
    log(f"skipped  {skipped:3d}  already complete")
    log(f"unknown  {len(unknown):3d}  not found in PortMaster")
    log(f"stale    {len(stale):3d}  gamelist entry with no matching file")

    if unknown:
        log()
        log("unknown ports (not from PortMaster - scrape these yourself or edit):")
        for e in sorted(unknown, key=lambda x: x.path):
            tag = f"  [{e.note}]" if e.note else ""
            log(f"  ./{e.path}{tag}")


def write_report(entries, stale, path, as_csv):
    rows = []
    for e in entries + stale:
        rows.append({
            "path": e.path,
            "bucket": e.bucket,
            "state": e.state or "",
            "name": (e.fields or {}).get("name", ""),
            "nested": "yes" if e.nested else "",
            "note": e.note,
        })
    rows.sort(key=lambda r: (r["bucket"], r["path"]))

    if as_csv:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["path", "bucket", "state",
                                               "name", "nested", "note"])
            w.writeheader()
            w.writerows(rows)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# pmscraper report\n\n")
            fh.write("| path | bucket | state | name | nested | note |\n")
            fh.write("|---|---|---|---|---|---|\n")
            for r in rows:
                fh.write("| {path} | {bucket} | {state} | {name} | "
                         "{nested} | {note} |\n".format(**{
                             k: str(v).replace("|", "\\|") for k, v in r.items()}))
    log(f"report written to {path}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Write PortMaster metadata into the ports gamelist.xml")
    ap.add_argument("--ports-dir", help="override the roms/ports directory")
    ap.add_argument("--cfg-dir", help="override the PortMaster/config directory")
    ap.add_argument("--apply", action="store_true",
                    help="actually write files (default is a dry run)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite values already present in gamelist.xml")
    ap.add_argument("--online", action="store_true",
                    help="fetch ports.json / missing artwork from GitHub")
    ap.add_argument("--prefer-covers", action="store_true",
                    help="use cover art as <image> (for themes without <thumbnail>)")
    ap.add_argument("--port-dates", action="store_true",
                    help="use the port's release date as <releasedate>")
    ap.add_argument("--only-missing", action="store_true",
                    help="only write entries that are missing or partial")
    ap.add_argument("--stub-unknown", action="store_true",
                    help="write a tidied <name> for unknown (non-PortMaster) .sh")
    ap.add_argument("--prune", action="store_true",
                    help="remove gamelist entries whose .sh no longer exists")
    ap.add_argument("--no-fuzzy", action="store_true",
                    help="treat title-only catalog matches as unknown")
    ap.add_argument("--report", metavar="FILE",
                    help="write the full classification breakdown to FILE")
    ap.add_argument("--csv", action="store_true",
                    help="make --report write CSV instead of Markdown")
    ap.add_argument("--progress", action="store_true",
                    help="post a per-port ES toast 'name [x/y]' while scraping "
                         "(only visible while ES is in the foreground)")
    ap.add_argument("--no-reload", action="store_true",
                    help="do not call ES /reloadgames after --apply")
    ap.add_argument("--restart-es", action="store_true",
                    help="restart EmulationStation when done (instead of reload)")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args(argv)

    ports_dir = find_ports_dir(args.ports_dir)
    cfg_dir = find_cfg_dir(ports_dir, args.cfg_dir)
    media_dir = ports_dir / "images"
    gamelist = ports_dir / "gamelist.xml"

    log(f"pmscraper {VERSION}")
    log(f"  ports dir : {ports_dir}")
    log(f"  config dir: {cfg_dir if cfg_dir else '(not found - artwork may be missing)'}")
    log(f"  gamelist  : {gamelist}")
    log(f"  mode      : {'APPLY' if args.apply else 'dry run'}"
        f"{' +online' if args.online else ''}{' +force' if args.force else ''}"
        f"{' +only-missing' if args.only_missing else ''}")
    log()

    images = index_images(cfg_dir)
    installed = scan_installed_ports(ports_dir)
    es_entries = enumerate_es_entries(ports_dir)
    log(f"ES sees {len(es_entries)} launcher(s); {len(installed)} port(s) have "
        f"port.json; {len(images)} artwork entr(ies) cached")

    catalog = {}
    if args.online:
        cache = (cfg_dir or ports_dir) / "pmscraper_ports.json"
        try:
            catalog = load_online_catalog(cache)
            log(f"catalog: {len(catalog)} ports known upstream")
        except Exception as err:
            log(f"  ! could not load catalog ({err}); continuing offline")

    entries = classify(es_entries, installed, catalog, use_fuzzy=not args.no_fuzzy)

    root = load_gamelist(gamelist)
    by_path = {}
    for game in root.findall("game"):
        node = game.find("path")
        if node is not None:
            by_path[normalise_path(node.text)] = game

    stale = find_stale(by_path, ports_dir)

    # Work out completeness and the fields to write for every entry. Skipping
    # complete entries under --only-missing *before* resolving art avoids the
    # copy/stat work the auto-run (game-end) hook would otherwise waste.
    for e in entries:
        e.state = completeness(by_path.get(e.path))

        if e.identifiable:
            if args.only_missing and e.state == "complete":
                continue
            art = resolve_art(e, images, media_dir, ports_dir, cfg_dir,
                              args.apply, args.online)
            e.fields = build_fields(e.info, e.scripts, e.path, art,
                                    args.port_dates, args.prefer_covers)
        elif e.bucket == "unknown" and args.stub_unknown and not e.nested:
            e.fields = {"name": tidy_name(Path(e.path).stem)}

    # Merge into the gamelist - uniform: write whatever fields an entry carries.
    changed = added = pruned = 0

    # Per-port progress toasts (opt-in): count the identified ports we'll write,
    # so the counter denominator matches what actually scrolls by.
    show_progress = args.progress and args.apply
    to_scrape = sum(1 for e in entries if e.identifiable and e.fields)
    done = 0

    for e in entries:
        if not e.fields:
            continue

        if show_progress and e.identifiable:
            done += 1
            notify_es(f"{e.fields['name']} [{done}/{to_scrape}] "
                      f"{progress_bar(done, to_scrape)}")

        game = by_path.get(e.path)
        touched = False
        if game is None:
            game = ET.SubElement(root, "game")
            ET.SubElement(game, "path").text = "./" + e.path
            by_path[e.path] = game
            added += 1
            touched = True
            log(f"  + {e.path}  ->  {e.fields.get('name', '')}")

        for tag in MANAGED_TAGS:
            if tag not in e.fields:
                continue
            node = game.find(tag)
            if node is None:
                node = ET.SubElement(game, tag)
            elif (node.text or "").strip() and not args.force:
                continue
            if (node.text or "") != e.fields[tag]:
                node.text = e.fields[tag]
                touched = True

        if touched:
            changed += 1

    if args.prune:
        for e in stale:
            game = by_path.get(e.path)
            if game is not None:
                root.remove(game)
                pruned += 1
                log(f"  - pruned {e.path} {e.note}")

    print_summary(entries, stale)
    log()
    log(f"{added} added, {changed} written"
        + (f", {pruned} pruned" if args.prune else ""))

    if args.report:
        write_report(entries, stale, args.report, args.csv)

    exit_code = 1 if any(e.bucket == "unknown" for e in entries) else 0

    if not args.apply:
        log("dry run - nothing was written. Re-run with --apply.")
        return exit_code

    if added or changed or pruned:
        had_gamelist = gamelist.is_file()
        write_gamelist(root, gamelist)
        log(f"wrote {gamelist}" + (f" (backup at {gamelist}.bak)" if had_gamelist else ""))

        if args.restart_es:
            restart_es()
        elif not args.no_reload:
            if reload_es():
                log("reloaded EmulationStation gamelists")
    else:
        log("nothing changed; gamelist left as-is")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
