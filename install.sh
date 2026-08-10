#!/bin/sh
# install.sh - put pmscraper on a KNULLI / Batocera device.
#
#   ./install.sh              install (or upgrade - it is idempotent)
#   ./install.sh --dry-run    print what it would do, change nothing
#   ./install.sh --uninstall  remove the three installed files
#   ./install.sh --ports-dir /path/to/roms/ports   override detection
#
# It installs four files:
#   pmscraper.py             -> <PortMaster>/pmscraper/pmscraper.py  (survives updates)
#   PortMaster Scraper.sh    -> <roms>/ports/                        (Ports-menu entry)
#   pmscraper-trigger.sh     -> <ES configs>/scripts/game-start/     (auto-run marker)
#   pmscraper-hook.sh        -> <ES configs>/scripts/game-end/       (auto-run)
#
# gamelist.xml, the images/ folder and any .bak are never touched.

set -eu

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

DRY=0
UNINSTALL=0
PORTS_DIR_OVERRIDE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)    DRY=1 ;;
        --uninstall)  UNINSTALL=1 ;;
        --ports-dir)  PORTS_DIR_OVERRIDE="${2:-}"; shift ;;
        --ports-dir=*) PORTS_DIR_OVERRIDE="${1#*=}" ;;
        -h|--help)
            sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then say "  would: $*"; else eval "$@"; fi; }

# --------------------------------------------------------------------------- #
# 1. Preflight
# --------------------------------------------------------------------------- #
step "Preflight"

if ! command -v python3 >/dev/null 2>&1; then
    say "error: python3 not found on PATH"; exit 1
fi
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
if ! python3 -c 'import sys;sys.exit(0 if sys.version_info[:2]>=(3,7) else 1)'; then
    say "error: python3 $PYV is too old (need >= 3.7)"; exit 1
fi
say "python3 $PYV: ok"

# Locate the ports dir the same way pmscraper does.
PORTS_DIR=""
for cand in \
    "$PORTS_DIR_OVERRIDE" \
    "${HM_PORTS_DIR:-}" \
    /userdata/roms/ports \
    /roms/ports \
    /storage/roms/ports
do
    [ -n "$cand" ] && [ -d "$cand" ] && { PORTS_DIR="$cand"; break; }
done
if [ -z "$PORTS_DIR" ]; then
    say "error: could not find a roms/ports directory; pass --ports-dir"; exit 1
fi

# PortMaster data dir (holds config/images_pm) - where pmscraper.py will live.
# Same ordered candidate list the game-end hook uses to find pmscraper.py again,
# and the order PortMaster's own control.txt resolves controlfolder - keep the
# three in sync so a fresh install lands where the consumers look.
XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
PM_DIR=""
for cand in \
    /opt/system/Tools/PortMaster \
    /opt/tools/PortMaster \
    "$XDG_DATA_HOME/PortMaster" \
    /userdata/system/.local/share/PortMaster \
    /roms/ports/PortMaster \
    /storage/roms/ports/PortMaster
do
    [ -d "$cand" ] && { PM_DIR="$cand"; break; }
done
[ -n "$PM_DIR" ] || PM_DIR=/userdata/system/.local/share/PortMaster

# ES user config dir (holds scripts/<event>/).
ES_DIR=""
for cand in \
    /userdata/system/configs/emulationstation \
    /storage/.config/emulationstation \
    "$HOME/.emulationstation"
do
    [ -d "$cand" ] && { ES_DIR="$cand"; break; }
done
[ -n "$ES_DIR" ] || ES_DIR=/userdata/system/configs/emulationstation

DEST_PY="$PM_DIR/pmscraper/pmscraper.py"
DEST_LAUNCH="$PORTS_DIR/PortMaster Scraper.sh"
DEST_TRIGGER="$ES_DIR/scripts/game-start/pmscraper-trigger.sh"
DEST_HOOK="$ES_DIR/scripts/game-end/pmscraper-hook.sh"

say "ports dir : $PORTS_DIR"
say "PortMaster: $PM_DIR"
say "ES configs: $ES_DIR"

# Sanity: refuse if the ports dir looks nothing like a ports dir.
if [ ! -e "$PORTS_DIR/PortMaster.sh" ] && [ -z "$(ls -1 "$PORTS_DIR" 2>/dev/null)" ]; then
    say "error: $PORTS_DIR has no PortMaster.sh and is empty - wrong dir?"; exit 1
fi

# --------------------------------------------------------------------------- #
# Uninstall
# --------------------------------------------------------------------------- #
if [ "$UNINSTALL" = 1 ]; then
    step "Uninstall"
    # Remove the scraper's own gamelist entry first - it needs pmscraper.py,
    # which we delete just below. PortMaster's entry and the rest are left.
    if [ "$DRY" = 1 ]; then
        say "  would: python3 \"$DEST_PY\" --ports-dir \"$PORTS_DIR\" --apply --unregister-self --no-reload"
    elif [ -f "$DEST_PY" ]; then
        python3 "$DEST_PY" --ports-dir "$PORTS_DIR" --apply --unregister-self --no-reload || true
    fi
    for f in "$DEST_PY" "$DEST_LAUNCH" "$DEST_TRIGGER" "$DEST_HOOK"; do
        if [ -e "$f" ]; then run "rm -f \"$f\""; say "  removed $f"
        else say "  (absent) $f"; fi
    done
    # Drop the now-empty pmscraper/ dir, but leave the rest alone.
    run "rmdir \"$PM_DIR/pmscraper\" 2>/dev/null || true"
    say ""
    say "Done. Removed the PortMaster Scraper entry; PortMaster's entry,"
    say "gamelist.xml, images/ and .bak were left untouched."
    exit 0
fi

# --------------------------------------------------------------------------- #
# 2. Filesystem check (exFAT can't carry the exec bit or Unix perms)
# --------------------------------------------------------------------------- #
step "Filesystem"
FSTYPE=$(stat -f -c %T "$PORTS_DIR" 2>/dev/null || echo "unknown")
say "ports partition fs: $FSTYPE"
case "$FSTYPE" in
    *exfat*|*msdos*|*vfat*|*fuseblk*|*ntfs*)
        say "WARNING: this filesystem can't carry the Unix exec bit, so ES will"
        say "         NOT run the auto-run hooks (game-start/game-end scripts"
        say "         require exec permission). The 'PortMaster Scraper' entry in"
        say "         the Ports menu still works - run it there by hand instead."
        say "         (KNULLI's ext4 SD layout does not have this limitation.)" ;;
esac

# --------------------------------------------------------------------------- #
# 3. Install files
# --------------------------------------------------------------------------- #
step "Install"

install_file() {   # src dest
    src="$1"; dest="$2"
    destdir=$(dirname -- "$dest")
    run "mkdir -p \"$destdir\""
    run "cp -f \"$src\" \"$dest\""
    say "  $dest"
}

install_file "$SELF_DIR/pmscraper.py"                     "$DEST_PY"
install_file "$SELF_DIR/ports/PortMaster Scraper.sh"      "$DEST_LAUNCH"
install_file "$SELF_DIR/hooks/game-start/pmscraper-trigger.sh" "$DEST_TRIGGER"
install_file "$SELF_DIR/hooks/game-end/pmscraper-hook.sh" "$DEST_HOOK"

# --------------------------------------------------------------------------- #
# 4. Permissions (verify, warn - never fail - if the fs drops the bit)
# --------------------------------------------------------------------------- #
# The ES event-script auto-run needs the exec bit; on exFAT/NTFS userdata ES
# cannot run these scripts at all (the Ports-menu launcher still works). On
# ext4 - the KNULLI default for SD cards - this is fine.
step "Permissions"
for f in "$DEST_PY" "$DEST_LAUNCH" "$DEST_TRIGGER" "$DEST_HOOK"; do
    run "chmod 755 \"$f\" 2>/dev/null || true"
    if [ "$DRY" = 0 ] && [ ! -x "$f" ]; then
        say "  warn: $f is not executable (exFAT?) - ES still runs it via sh"
    else
        say "  ok: $f"
    fi
done

# --------------------------------------------------------------------------- #
# 5. Register the tool launchers so PortMaster + the Scraper read as real
#    entries in the Ports menu, not bare filenames.
# --------------------------------------------------------------------------- #
step "Register tool launchers in the gamelist"
if [ "$DRY" = 1 ]; then
    say "  would: python3 \"$DEST_PY\" --ports-dir \"$PORTS_DIR\" --apply --register-tools --no-reload"
else
    python3 "$DEST_PY" --ports-dir "$PORTS_DIR" --apply --register-tools --no-reload || true
fi

# --------------------------------------------------------------------------- #
# 6. Verify - a real dry run proves it can see your ports
# --------------------------------------------------------------------------- #
step "Verify (dry run)"
if [ "$DRY" = 1 ]; then
    say "  would: python3 \"$DEST_PY\" --ports-dir \"$PORTS_DIR\""
else
    python3 "$DEST_PY" --ports-dir "$PORTS_DIR" || true
fi

say ""
say "Installed. Run it from the Ports menu ('PortMaster Scraper'), or over SSH:"
say "  python3 \"$DEST_PY\" --apply"
say "New ports are scraped automatically when you exit PortMaster."
