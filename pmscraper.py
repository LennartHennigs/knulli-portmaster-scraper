#!/usr/bin/env python3
"""
pmscraper - a PortMaster "scraper" for KNULLI / Batocera-based firmware.

Instead of asking ScreenScraper/TheGamesDB about .sh launcher scripts (which they
do not know about), this reads the metadata PortMaster already put on the SD card
and writes it straight into /userdata/roms/ports/gamelist.xml.

Sources of truth, in order:
  1. <ports>/<portdir>/gameinfo.xml       - porter-authored editorial text,
                                             when the port ships one
  2. <ports>/<portdir>/port.json          - written by PortMaster on install
  3. <ports>/<portdir>/cover.*            - cover art shipped with the port
  4. <tools>/PortMaster/config/images_pm/ - artwork cache (images.zip)
  5. --online: ports.json + screenshots from the PortMaster GitHub release

gameinfo.xml is not, as the name might suggest, something PortMaster's own
KNULLI gamelist writer produces - it is a <gameList><game> document some
porters ship directly inside their port's own directory, alongside
port.json, with real editorial text (game description, real developer/
publisher, release date) instead of port.json's short install blurb. Only
its text fields are used; its <image> tag is ignored (see parse_gameinfo).

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

VERSION = "1.3.0"

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
    "developer", "publisher", "releasedate", "rating", "players",
)

# Our own Ports-menu launchers (referenced by name in --unregister-self).
SCRAPER_LAUNCHER = "PortMaster Scraper.sh"
SCRAPER_FORCE_LAUNCHER = "PortMaster Scraper (Rescan All).sh"
SCRAPER_LAUNCHERS = (SCRAPER_LAUNCHER, SCRAPER_FORCE_LAUNCHER)

# The tool launchers (not scrapeable games): metadata written by --register-tools
# so they read as proper entries instead of bare filenames, and the single source
# of truth for which launchers to skip during enumeration. Only launchers present
# on disk are registered.
TOOL_ENTRIES = {
    "PortMaster.sh": {
        "name": "PortMaster",
        "desc": ("Install and manage community game ports. Browse the library, "
                 "download ports, and manage what's installed on your device."),
        "genre": "Utility",
        "publisher": "PortMaster",
    },
    SCRAPER_LAUNCHER: {
        "name": "PortMaster Scraper",
        "desc": ("Scrapes names, descriptions, genres and box art for your "
                 "installed PortMaster ports and writes them into the Ports "
                 "gamelist. Run it after installing ports - new installs are "
                 "also picked up automatically when you exit PortMaster."),
        "genre": "Utility",
        "publisher": "PortMaster",
    },
    SCRAPER_FORCE_LAUNCHER: {
        "name": "PortMaster Scraper (Rescan All)",
        "desc": ("Re-scrapes every installed port and overwrites existing "
                 "gamelist data with freshly-resolved names, descriptions, "
                 "art and other fields. Use this after updating pmscraper, "
                 "or if a port's metadata looks stale."),
        "genre": "Utility",
        "publisher": "PortMaster",
    },
}

# Tools are skipped in enumeration so they never show up as "unknown".
SKIP_LAUNCHERS = frozenset(TOOL_ENTRIES)

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


# Where human-readable log lines go. --emit-progress reroutes them to stderr so
# stdout carries only machine-readable PMPROG lines for the pugwash launcher.
_LOG_STREAM = sys.stdout


def log(msg=""):
    print(msg, file=_LOG_STREAM, flush=True)


def die(msg):
    """Setup/config error → exit 2 (distinct from 1 = 'unknowns present') so the
    launcher can tell a misconfiguration from a normal run."""
    print(msg, file=sys.stderr, flush=True)
    sys.exit(2)


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
            die(f"error: --ports-dir {p} is not a directory")
        return p

    env = os.environ.get("HM_PORTS_DIR")
    if env and Path(env).is_dir():
        return Path(env)

    for cand in PORTS_DIR_CANDIDATES:
        p = Path(cand)
        if p.is_dir():
            return p

    die("error: could not find a ports directory; pass --ports-dir")


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
        # Prune hidden dirs and PortMaster's own tooling tree (on ArkOS/JELOS-style
        # layouts PortMaster lives under roms/ports; its internal .sh aren't games).
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d != "PortMaster"]
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
    """Every <ports>/<dir>/port.json PortMaster wrote at install time, plus
    any gameinfo.xml a porter shipped beside it (see parse_gameinfo)."""
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

        info["_portdir"] = pf.parent
        gameinfo = pf.parent / "gameinfo.xml"
        if gameinfo.is_file():
            info["_gameinfo"] = parse_gameinfo(gameinfo)

        try:
            mtime = pf.stat().st_mtime   # when PortMaster last wrote this port
        except OSError:
            mtime = 0.0
        ports.append((info, scripts, mtime))

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


def local_cover(info, portdir):
    """A cover file the porter shipped directly in the port's own directory -
    no images_pm cache or --online needed. Tries port.json's declared
    covers[] filename first, then the conventional `cover.*` name many ports
    use even when port.json declares none (e.g. descent, descent2, doom3,
    masseffect all ship a local cover.png with no covers[] entry at all)."""
    if portdir is None:
        return None
    img = (info.get("attr") or {}).get("image")
    if isinstance(img, dict):
        for c in (img.get("covers") or []):
            p = portdir / c
            if p.is_file():
                return p
    for p in sorted(portdir.glob("cover.*")):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg"):
            return p
    return None


GAMEINFO_TAGS = ("name", "desc", "genre", "developer", "publisher",
                  "releasedate", "rating", "players")


def parse_gameinfo(path):
    """<portdir>/gameinfo.xml, if the porter shipped one: a real
    <gameList><game> doc (same shape pmscraper writes) with editorial text
    richer than port.json's blurb. Only text tags are used - see
    GAMEINFO_TAGS; <image> is deliberately ignored (it inconsistently holds a
    cover or a screenshot with no way to tell which, unlike port.json's
    typed image dict)."""
    try:
        game = ET.parse(str(path)).getroot().find("game")
    except (OSError, ET.ParseError) as err:
        log(f"  ! skipping {path}: {err}")
        return {}
    if game is None:
        return {}
    out = {}
    for tag in GAMEINFO_TAGS:
        text = (game.findtext(tag) or "").strip()
        if text:
            out[tag] = text
    return out


# --------------------------------------------------------------------------- #
# gamelist.xml
# --------------------------------------------------------------------------- #

def build_fields(info, scripts, path, art, port_dates=False, prefer_covers=False):
    attr = info.get("attr") or {}
    gi = info.get("_gameinfo") or {}

    if len(scripts) > 1:
        name = Path(path).stem
    else:
        name = gi.get("name") or attr.get("title") or Path(path).stem

    fields = {"name": name}

    desc = (gi.get("desc") or attr.get("desc") or "").strip()
    inst = (attr.get("inst") or "").strip()
    if desc and inst and inst.lower() not in ("ready to run.", "ready to run"):
        desc = f"{desc}\n\n{inst}"
    if desc:
        fields["desc"] = desc

    if gi.get("genre"):
        # Already ES-styled (e.g. "Shooter / 1st person-Action-Shooter") -
        # unlike port.json's raw lowercase genre keys, skip GENRE_FIXUPS.
        fields["genre"] = gi["genre"]
    genres = [g for g in (attr.get("genres") or []) if g]
    if genres:
        if "genre" not in fields:
            fields["genre"] = ", ".join(GENRE_FIXUPS.get(g, g.title()) for g in genres)
        # Raw genres for ES's tag-based filtering - gameinfo has no
        # raw-keyword equivalent, so this always comes from port.json.
        fields["tags"] = ", ".join(genres)

    if gi.get("developer"):
        fields["developer"] = gi["developer"]
    else:
        porters = [p for p in (attr.get("porter") or []) if p]
        if porters:
            fields["developer"] = ", ".join(porters)
    fields["publisher"] = gi.get("publisher") or "PortMaster"

    if gi.get("players"):
        fields["players"] = gi["players"]

    # gameinfo's releasedate is the game's real release date, already in
    # gamelist format - unlike --port-dates below, no "misleading" caveat.
    gi_date = gi.get("releasedate") or ""
    if re.match(r"^\d{8}T\d{6}$", gi_date):
        fields["releasedate"] = gi_date
    elif port_dates:
        # date_added is when the *port* landed, not when the game came out,
        # so this is opt-in - useful for "sort by newest port", misleading
        # otherwise.
        source = info.get("source") or {}
        added = source.get("date_added") or ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", added):
            fields["releasedate"] = added.replace("-", "") + "T000000"

    gi_rating = None
    try:
        if gi.get("rating") is not None:
            gi_rating = float(gi["rating"])
    except (TypeError, ValueError):
        gi_rating = None
    if gi_rating is not None and 0.0 <= gi_rating <= 1.0:
        fields["rating"] = f"{gi_rating:.4f}"
    else:
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
    # Keep the slots true to their meaning: <image> = screenshot, <thumbnail>
    # ("Box") = cover art only - never a screenshot in the box slot. --prefer-covers
    # swaps them for themes that render <image> as the primary art.
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


def index_by_path(root):
    """Map each <game>'s normalised <path> to its element (skipping path-less)."""
    return {normalise_path(g.findtext("path")): g
            for g in root.findall("game") if g.find("path") is not None}


def commit_gamelist(root, gamelist, changed, apply, reload_when_done,
                    noop_msg, dry_msg, apply_msg=None):
    """Shared tail for the write actions: report a no-op, write+reload under
    --apply, or announce a dry run. Returns 0."""
    if not changed:
        log(noop_msg)
    elif apply:
        write_gamelist(root, gamelist)
        if apply_msg:
            log(apply_msg)
        if reload_when_done:
            reload_es()
    else:
        log(dry_msg)
    return 0


def merge_fields(root, by_path, path, fields, force=False):
    """Non-destructively merge `fields` into the <game> for `path`, creating it
    if needed. Returns True if anything changed. Shared by the scrape and
    --register-tools paths."""
    game = by_path.get(path)
    touched = False
    if game is None:
        game = ET.SubElement(root, "game")
        ET.SubElement(game, "path").text = "./" + path
        by_path[path] = game
        touched = True
    for tag, val in fields.items():
        node = game.find(tag)
        if node is None:
            node = ET.SubElement(game, tag)
        elif (node.text or "").strip() and not force:
            continue
        if (node.text or "") != val:
            node.text = val
            touched = True
    return touched


def register_tools(ports_dir, gamelist, apply, reload_when_done=True):
    """Give the tool launchers (PortMaster, PortMaster Scraper) tidy gamelist
    entries. Only registers launchers that exist on disk. Like the scrape, this
    only writes with --apply; without it, it just reports what it would add."""
    root = load_gamelist(gamelist)
    by_path = index_by_path(root)
    changed = False
    for launcher, fields in TOOL_ENTRIES.items():
        if not (ports_dir / launcher).is_file():
            continue
        if merge_fields(root, by_path, launcher, fields):
            log(f"{'registered' if apply else 'would register'} '{fields['name']}'")
            changed = True
    return commit_gamelist(root, gamelist, changed, apply, reload_when_done,
                           "tool launchers already registered",
                           "dry run - nothing written. Re-run with --apply.")


def unregister_self(gamelist, apply, reload_when_done=True):
    """Remove the scraper's own gamelist entries (used by install.sh
    --uninstall). Leaves PortMaster's entry alone - PortMaster stays
    installed."""
    root = load_gamelist(gamelist)
    by_path = index_by_path(root)
    games = [by_path[name] for name in SCRAPER_LAUNCHERS if name in by_path]
    if games and apply:
        for game in games:
            root.remove(game)
    return commit_gamelist(root, gamelist, bool(games), apply,
                           reload_when_done,
                           "no PortMaster Scraper entry to remove",
                           "would remove the PortMaster Scraper gamelist entry(ies)",
                           apply_msg="removed the PortMaster Scraper gamelist entry(ies)")


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


def emit_progress(done, total, name, toast, emit):
    """Report scraping progress. `toast` sends an ES popup 'name [m/n]' (no ASCII
    bar - it renders badly in a popup; foreground-only); `emit` prints a machine
    line to stdout for the pugwash launcher to turn into an on-screen bar. Both
    are best-effort."""
    if toast:
        notify_es(f"{name} [{done}/{total}]")
    if emit:
        # Tab-separated; name is last and never contains a tab. Real stdout.
        sys.stdout.write(f"PMPROG\t{done}\t{total}\t{name}\n")
        sys.stdout.flush()


def restart_es():
    """Restart EmulationStation so it reloads gamelists from disk. Needed when
    the lightweight /reloadgames won't repaint - e.g. run as a Ports-menu launch
    with ES backgrounded. KNULLI ships knulli-es-swissknife; the universal
    fallback is GET /quit (KNULLI's boot loop relaunches ES)."""
    for cmd in (["knulli-es-swissknife", "--restart"],
                ["batocera-es-swissknife", "--restart"]):
        if shutil.which(cmd[0]):
            log(f"running: {' '.join(cmd)}")
            subprocess.run(cmd, check=False)
            return True
    # No swissknife: ask ES to quit; the init loop brings it back up.
    try:
        fetch(f"http://{ES_HOST}:{ES_PORT}/quit", timeout=5)
        log("asked ES to quit (it restarts and reloads from disk)")
        return True
    except Exception as err:
        log(f"  ! could not restart ES ({err})")
        return False


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

class Entry:
    """One thing ES would show, plus what pmscraper worked out about it."""

    __slots__ = ("path", "nested", "bucket", "info", "scripts", "mtime",
                 "state", "fields", "note")

    def __init__(self, path, nested=False):
        self.path = path          # normalised, e.g. "Balatro.sh" - also the
                                  # .sh used for naming + art (one entry = one .sh)
        self.nested = nested
        self.bucket = "unknown"   # port.json | catalog | fuzzy | unknown | stale
        self.info = None          # port.json / catalog dict for the source port
        self.scripts = None       # sibling launchers (multi-launcher naming)
        self.mtime = 0.0          # port.json mtime (0 for catalog/fuzzy) - --since
        self.state = None         # missing | partial | complete
        self.fields = None        # built gamelist fields
        self.note = ""            # human hint for the report

    @property
    def identifiable(self):
        return self.bucket in ("port.json", "catalog", "fuzzy")


def classify(es_entries, installed, catalog, use_fuzzy):
    """Turn the raw ES entry set into classified Entry objects."""
    # script basename -> (info, scripts, mtime)
    by_script = {}
    for info, scripts, mtime in installed:
        for s in scripts:
            by_script[normalise_path(s)] = (info, scripts, mtime)

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
            e.info, e.scripts, e.mtime = by_script[path]
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

    if "cover" not in art_src:
        got = local_cover(e.info, e.info.get("_portdir"))
        if got:
            art_src["cover"] = got

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
        # "scraped" = resolved this run (e.fields set); "skipped" = already
        # complete. An eligible port that --since/--only-missing filtered out is
        # neither (e.fields is None and it isn't complete) - it wasn't touched.
        if e.state == "complete":
            skipped += 1
        elif e.fields:
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
    ap.add_argument("--since", type=float, metavar="EPOCH",
                    help="only scrape ports whose port.json was written at/after "
                         "this Unix time (the auto-run hook's 'new ports only')")
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
    ap.add_argument("--emit-progress", action="store_true",
                    help="print machine-readable 'PMPROG' progress lines to "
                         "stdout (log goes to stderr) for the pugwash launcher")
    ap.add_argument("--no-reload", action="store_true",
                    help="do not call ES /reloadgames after --apply")
    ap.add_argument("--restart-es", action="store_true",
                    help="restart EmulationStation when done (instead of reload)")
    ap.add_argument("--register-tools", action="store_true",
                    help="just add gamelist entries for the tool launchers "
                         "(PortMaster, PortMaster Scraper); used by install.sh, "
                         "then exit")
    ap.add_argument("--unregister-self", action="store_true",
                    help="remove the scraper's own gamelist entry (used by "
                         "install.sh --uninstall), then exit")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args(argv)

    if args.emit_progress:
        # Keep stdout clean for PMPROG lines; human log goes to stderr.
        global _LOG_STREAM
        _LOG_STREAM = sys.stderr

    ports_dir = find_ports_dir(args.ports_dir)
    cfg_dir = find_cfg_dir(ports_dir, args.cfg_dir)
    media_dir = ports_dir / "images"
    gamelist = ports_dir / "gamelist.xml"

    if args.register_tools:
        # Standalone: register the tool launchers and stop (no scrape).
        return register_tools(ports_dir, gamelist, args.apply,
                              reload_when_done=not args.no_reload)

    if args.unregister_self:
        return unregister_self(gamelist, args.apply,
                               reload_when_done=not args.no_reload)

    log(f"pmscraper {VERSION}")
    log(f"  ports dir : {ports_dir}")
    log(f"  config dir: {cfg_dir if cfg_dir else '(not found - artwork may be missing)'}")
    log(f"  gamelist  : {gamelist}")
    log(f"  mode      : {'APPLY' if args.apply else 'dry run'}"
        f"{' +online' if args.online else ''}{' +force' if args.force else ''}"
        f"{' +only-missing' if args.only_missing else ''}"
        f"{' +since' if args.since is not None else ''}")
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
    by_path = index_by_path(root)

    stale = find_stale(by_path, ports_dir)

    for e in entries:
        e.state = completeness(by_path.get(e.path))

    # Resolve art + fields. This is where --online spends its time (downloads),
    # so progress is reported here. Skipping complete entries under
    # --only-missing *before* resolving avoids copy/stat work we'd throw away.
    to_resolve = [e for e in entries if e.identifiable
                  and not (args.only_missing and e.state == "complete")
                  and not (args.since is not None and e.mtime < args.since)]
    total = len(to_resolve)
    for i, e in enumerate(to_resolve, 1):
        name = (e.info.get("attr") or {}).get("title") or Path(e.path).stem
        emit_progress(i, total, name, toast=args.progress, emit=args.emit_progress)
        art = resolve_art(e, images, media_dir, ports_dir, cfg_dir,
                          args.apply, args.online)
        e.fields = build_fields(e.info, e.scripts, e.path, art,
                                args.port_dates, args.prefer_covers)

    for e in entries:
        if (e.bucket == "unknown" and args.stub_unknown and not e.nested
                and not e.fields):
            e.fields = {"name": tidy_name(Path(e.path).stem)}

    # Merge into the gamelist - uniform: write whatever fields an entry carries.
    # Shares merge_fields() with --register-tools so the non-destructive per-tag
    # rule lives in one place.
    changed = added = pruned = 0

    for e in entries:
        if not e.fields:
            continue
        is_new = e.path not in by_path
        if merge_fields(root, by_path, e.path, e.fields, force=args.force):
            changed += 1
            if is_new:
                added += 1
                log(f"  + {e.path}  ->  {e.fields.get('name', '')}")

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
    # Exit codes: 0 = clean, 1 = ok but some launchers were unidentified,
    # 2 = an unexpected error (so callers can tell a crash from "unknowns").
    # SystemExit (from sys.exit/die) is a BaseException, so it passes through
    # the Exception handler untouched - a bad --ports-dir still exits 2 via die().
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
