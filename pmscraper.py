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

After --apply, in EmulationStation:  START -> Game Settings -> Update Gamelists
"""

import argparse
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

VERSION = "1.0.0"

PORTS_JSON_URL = "https://github.com/PortsMaster/PortMaster-New/releases/latest/download/ports.json"
RAW_PORT_URL = "https://raw.githubusercontent.com/PortsMaster/PortMaster-New/main/ports/{port}/{file}"

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
    "name", "desc", "image", "thumbnail", "genre",
    "developer", "publisher", "releasedate", "rating",
)

GENRE_FIXUPS = {
    "fps": "FPS",
    "rpg": "RPG",
    "casino/card": "Casino/Card",
    "visual novel": "Visual Novel",
}


def log(msg=""):
    print(msg, flush=True)


def name_cleaner(text):
    """Mirrors harbourmaster.util.name_cleaner so image lookups line up."""
    temp = re.sub(r"[^a-zA-Z0-9 _\-\.]+", "", text.strip().lower())
    return re.sub(r"[ \.]+", ".", temp)


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
            log(f"  ! {info.get('name', pf.parent.name)}: no launcher .sh found, skipping")
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
    name = info.get("name") or ""
    if name.lower().endswith(".zip"):
        name = name[:-4]
    return name_cleaner(name)


# --------------------------------------------------------------------------- #
# Online fill-in
# --------------------------------------------------------------------------- #

def fetch(url, binary=False, timeout=30):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"pmscraper/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return data if binary else data.decode("utf-8")


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
    img = (info.get("attr") or {}).get("image") or {}
    if kind == "screenshot":
        fname = img.get("screenshot")
    else:
        covers = img.get("covers") or []
        fname = covers[0] if covers else None

    if not fname:
        return None

    name = info.get("name") or ""
    portdir = name[:-4] if name.lower().endswith(".zip") else name
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

def build_fields(info, scripts, script, art, port_dates=False):
    attr = info.get("attr") or {}

    if len(scripts) > 1:
        name = Path(script).stem
    else:
        name = attr.get("title") or Path(script).stem

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
        if avg is not None and float(mx or 5) > 0:
            fields["rating"] = f"{max(0.0, min(1.0, float(avg) / float(mx or 5))):.4f}"
    except (TypeError, ValueError):
        pass

    if art.get("screenshot"):
        fields["image"] = art["screenshot"]
    if art.get("cover"):
        fields["thumbnail"] = art["cover"]

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
    ap.add_argument("--port-dates", action="store_true",
                    help="use the port's release date as <releasedate>")
    ap.add_argument("--restart-es", action="store_true",
                    help="restart EmulationStation when done")
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
        f"{' +online' if args.online else ''}{' +force' if args.force else ''}")
    log()

    images = index_images(cfg_dir)
    ports = scan_installed_ports(ports_dir)
    log(f"found {len(ports)} installed port(s), {len(images)} artwork entr(ies) cached")

    catalog = {}
    if args.online:
        cache = (cfg_dir or ports_dir) / "pmscraper_ports.json"
        try:
            catalog = load_online_catalog(cache)
            log(f"catalog: {len(catalog)} ports known upstream")
        except Exception as err:
            log(f"  ! could not load catalog ({err}); continuing offline")

    # Ports with no port.json at all (hand-installed): recover from the catalog.
    if catalog:
        known = {port_stem(info) for info, _ in ports}
        claimed = {s for _, scripts in ports for s in scripts}
        for sh in sorted(p.name for p in ports_dir.glob("*.sh")):
            if sh in claimed or sh == "PortMaster.sh":
                continue
            guess = name_cleaner(Path(sh).stem)
            match = catalog.get(guess + ".zip") or catalog.get(guess)
            if match and port_stem(match) not in known:
                log(f"  + recovered {sh} from catalog")
                ports.append((match, [sh]))

    root = load_gamelist(gamelist)
    by_path = {}
    for game in root.findall("game"):
        node = game.find("path")
        if node is not None:
            by_path[normalise_path(node.text)] = game

    changed = added = 0

    for info, scripts in ports:
        stem = port_stem(info)
        art_src = dict(images.get(stem, {}))

        if args.online and args.apply and cfg_dir is not None:
            for kind in ("screenshot", "cover"):
                if kind not in art_src:
                    got = download_art(info, kind, cfg_dir / "images_pm")
                    if got:
                        art_src[kind] = got

        for script in scripts:
            art = {}
            for kind, tag in (("screenshot", "screenshot"), ("cover", "cover")):
                src = art_src.get(kind)
                if src is None:
                    continue
                suffix = "image" if kind == "screenshot" else "thumb"
                dest = media_dir / f"{Path(script).stem}-{suffix}{src.suffix.lower()}"
                if args.apply:
                    media_dir.mkdir(parents=True, exist_ok=True)
                    if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
                        shutil.copy2(str(src), str(dest))
                art[tag] = "./" + str(dest.relative_to(ports_dir))

            fields = build_fields(info, scripts, script, art, args.port_dates)
            key = normalise_path(script)
            game = by_path.get(key)

            if game is None:
                game = ET.SubElement(root, "game")
                ET.SubElement(game, "path").text = "./" + key
                by_path[key] = game
                added += 1
                touched = True
                log(f"  + {key}  ->  {fields['name']}")
            else:
                touched = False

            for tag in MANAGED_TAGS:
                if tag not in fields:
                    continue
                node = game.find(tag)
                if node is None:
                    node = ET.SubElement(game, tag)
                elif (node.text or "").strip() and not args.force:
                    continue
                if (node.text or "") != fields[tag]:
                    node.text = fields[tag]
                    touched = True

            if touched:
                changed += 1

    log()
    log(f"{added} entr(ies) added, {changed} entr(ies) written")

    if not args.apply:
        log("dry run - nothing was written. Re-run with --apply.")
        return 0

    had_gamelist = gamelist.is_file()
    write_gamelist(root, gamelist)
    log(f"wrote {gamelist}" + (f" (backup at {gamelist}.bak)" if had_gamelist else ""))

    if args.restart_es:
        # Fixed argv, no shell, no user input - avoids any command-injection sink.
        for cmd in (["batocera-es-swissknife", "--restart"],):
            if shutil.which(cmd[0]):
                log(f"running: {' '.join(cmd)}")
                subprocess.run(cmd, check=False)
                break
    else:
        log("Now in EmulationStation: START -> Game Settings -> Update Gamelists")

    return 0


if __name__ == "__main__":
    sys.exit(main())
