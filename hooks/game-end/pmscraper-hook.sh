#!/bin/sh
# pmscraper game-end hook for EmulationStation (KNULLI / Batocera).
#
# ES fires game-end with NO arguments, so we can't tell here what just exited.
# The game-start trigger drops /tmp/pmscraper.trigger when PortMaster launches;
# we act only if that marker is present, then clear it. Any other game exit
# returns in a couple of syscalls, so normal play is unaffected.
#
# Installed by install.sh to:
#   <ES configs>/scripts/game-end/pmscraper-hook.sh

MARKER=/tmp/pmscraper.trigger
[ -f "$MARKER" ] || exit 0
rm -f "$MARKER"

# Locate pmscraper.py (install.sh puts it beside PortMaster's own files).
XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
PMSCRAPER=""
for cand in \
    "$XDG_DATA_HOME/PortMaster/pmscraper/pmscraper.py" \
    /userdata/system/.local/share/PortMaster/pmscraper/pmscraper.py \
    /opt/system/Tools/PortMaster/pmscraper/pmscraper.py \
    /roms/ports/PortMaster/pmscraper/pmscraper.py \
    /storage/roms/ports/PortMaster/pmscraper/pmscraper.py
do
    [ -f "$cand" ] && PMSCRAPER="$cand" && break
done
[ -n "$PMSCRAPER" ] || exit 0

LOG=/tmp/pmscraper-hook.log

# --only-missing keeps the auto-run fast: it fills just the ports the fresh
# install added. pmscraper itself calls ES /reloadgames when anything changed.
python3 "$PMSCRAPER" --apply --only-missing --report "$LOG" >>"$LOG" 2>&1

# Toast the result on the handheld (best-effort; some builds lack /notify).
SUMMARY=$(grep -E '^(scraped|unknown)' "$LOG" | tr '\n' ' ')
if [ -n "$SUMMARY" ]; then
    curl -s -m 3 -X POST --data "pmscraper: $SUMMARY" \
        "http://127.0.0.1:1234/notify" >/dev/null 2>&1 || true
fi

exit 0
