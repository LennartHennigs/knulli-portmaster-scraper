#!/bin/bash
# Self-extracting installer for the KNULLI PortMaster Scraper.
#
# This is the HEAD of "Install PortMaster Scraper.sh" - make-release.sh appends a
# gzipped tarball of the payload (pmscraper.py, install.sh, ports/, hooks/) after
# the PAYLOAD marker below. Drop the built file into <roms>/ports/ and run it from
# the Ports menu: it extracts the payload to a temp dir, runs install.sh, removes
# itself, and restarts EmulationStation. No SSH needed.
#
# It also runs fine over SSH, and forwards any args to install.sh - e.g.
#   ./Install\ PortMaster\ Scraper.sh --dry-run --ports-dir /path/to/roms/ports

set -u

LOG=/tmp/pmscraper-install.log
: > "$LOG"

toast() {   # best-effort ES on-screen notifier (shows once ES is foreground)
    curl -s -m 3 -X POST --data "$1" "http://127.0.0.1:1234/notify" >/dev/null 2>&1 || true
}

fail() {
    echo "$1" | tee -a "$LOG" >&2
    toast "PortMaster Scraper: install failed - see $LOG"
    exit 1
}

# --- extract the appended payload ------------------------------------------ #
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")
MARKER=$(awk '/^__PMSCRAPER_PAYLOAD__$/ { print NR + 1; exit }' "$SELF")
[ -n "$MARKER" ] || fail "installer is corrupt (no payload marker)"

TMP=$(mktemp -d /tmp/pmscraper-install.XXXXXX) || fail "cannot make a temp dir"
trap 'rm -rf "$TMP"' EXIT

tail -n +"$MARKER" "$SELF" | tar xzf - -C "$TMP" >>"$LOG" 2>&1 \
    || fail "could not extract the payload"
[ -f "$TMP/install.sh" ] || fail "payload is missing install.sh"

# --- run the real installer (forwards --dry-run / --ports-dir / etc.) ------ #
echo "== running install.sh $*" >>"$LOG"
sh "$TMP/install.sh" "$@" >>"$LOG" 2>&1
RC=$?

if [ "$RC" -ne 0 ]; then
    fail "install.sh exited $RC"
fi

# --- success: remove the installer from the Ports folder ------------------- #
# (skip cleanup on a --dry-run so repeated dry runs keep working)
case " $* " in
    *" --dry-run "*) DRY=1 ;;
    *) DRY=0 ;;
esac
if [ "$DRY" = 0 ]; then
    rm -f "$SELF" >>"$LOG" 2>&1 || true
fi

echo "== done" >>"$LOG"
toast "PortMaster Scraper installed - see it in the Ports menu"

# --- restart ES so the new entry appears and this installer drops from the
#     menu (mirrors pmscraper's restart_es: swissknife, else HTTP /quit). ---- #
if [ "$DRY" = 0 ]; then
    if command -v knulli-es-swissknife >/dev/null 2>&1; then
        knulli-es-swissknife --restart >>"$LOG" 2>&1 || true
    elif command -v batocera-es-swissknife >/dev/null 2>&1; then
        batocera-es-swissknife --restart >>"$LOG" 2>&1 || true
    else
        curl -s -m 3 "http://127.0.0.1:1234/quit" >/dev/null 2>&1 || true
    fi
fi

exit 0

# Anything below this line is data, never executed (the `exit 0` above stops the
# shell). make-release.sh appends "__PMSCRAPER_PAYLOAD__" + a gzipped tarball.
