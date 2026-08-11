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

ES=http://127.0.0.1:1234
TITLE="PortMaster Scraper"   # gains " v<x.y.z>" once the payload is extracted

toast() {   # best-effort ES on-screen notifier (shows once ES is foreground)
    curl -s -m 3 -X POST --data "$1" "$ES/notify" >/dev/null 2>&1 || true
}

es_up() {   # any reply, even a 404, means ES's HTTP server is listening
    curl -s -m 2 -o /dev/null "$ES/"
}

# A toast posted from here never paints: this script runs as a "game" launched
# from the Ports menu, so ES is backgrounded - and we restart it right after.
# So hand the message to a detached waiter that waits for ES to answer again,
# gives the UI a moment to reach the foreground, and only then posts. That is
# why the install used to look like it "just reboots" with nothing to show.
# Polling is deliberately slow: ES is reloading a ~1400-entry gamelist off SD
# while this runs, so a per-second curl would just add contention.
toast_later() {   # $1 = message, $2 = 1 to wait out an ES restart first
    (
        if [ "${2:-0}" = 1 ]; then
            # Let the restart take ES down first, else we would toast into the
            # instance that is on its way out.
            i=0
            while es_up && [ "$i" -lt 10 ]; do i=$((i + 1)); sleep 2; done
        fi
        i=0
        while ! es_up && [ "$i" -lt 30 ]; do i=$((i + 1)); sleep 3; done
        sleep 4          # let ES finish loading and take the foreground
        toast "$1"
    ) >/dev/null 2>&1 &
}

fail() {
    echo "$1" | tee -a "$LOG" >&2
    toast_later "$TITLE: install failed - see $LOG"
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

# Version for the toasts - read straight out of the payload, no python needed
# (same one-liner make-release.sh uses to name the build).
PMVER=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$TMP/pmscraper.py" 2>/dev/null | head -1)
[ -n "$PMVER" ] && TITLE="PortMaster Scraper v$PMVER"

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

# --- restart ES so the new entry appears and this installer drops from the
#     menu (mirrors pmscraper's restart_es: swissknife, else HTTP /quit). The
#     success toast is queued first but only fires once ES is back up. -------- #
if [ "$DRY" = 0 ]; then
    toast_later "$TITLE installed - see it in the Ports menu" 1

    if command -v knulli-es-swissknife >/dev/null 2>&1; then
        knulli-es-swissknife --restart >>"$LOG" 2>&1 || true
    elif command -v batocera-es-swissknife >/dev/null 2>&1; then
        batocera-es-swissknife --restart >>"$LOG" 2>&1 || true
    else
        curl -s -m 3 "$ES/quit" >/dev/null 2>&1 || true
    fi
fi

exit 0

# Anything below this line is data, never executed (the `exit 0` above stops the
# shell). make-release.sh appends "__PMSCRAPER_PAYLOAD__" + a gzipped tarball.
