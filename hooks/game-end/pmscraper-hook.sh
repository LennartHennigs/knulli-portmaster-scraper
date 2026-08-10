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
SINCE=$(cat "$MARKER" 2>/dev/null)
rm -f "$MARKER"

# Locate pmscraper.py (install.sh puts it beside PortMaster's own files). Same
# ordered PortMaster-dir list install.sh installs into - keep the two in sync.
XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
PMSCRAPER=""
for base in \
    /opt/system/Tools/PortMaster \
    /opt/tools/PortMaster \
    "$XDG_DATA_HOME/PortMaster" \
    /userdata/system/.local/share/PortMaster \
    /roms/ports/PortMaster \
    /storage/roms/ports/PortMaster
do
    [ -f "$base/pmscraper/pmscraper.py" ] && PMSCRAPER="$base/pmscraper/pmscraper.py" && break
done
[ -n "$PMSCRAPER" ] || exit 0

LOG=/tmp/pmscraper-hook.log

# Scrape only ports installed during this PortMaster session (port.json newer
# than the launch time the game-start hook recorded) - not a full rescan. If the
# timestamp is missing for any reason, fall back to --only-missing. --progress
# toasts each port (ES is foreground here, so it shows); pmscraper reloads ES
# when anything changed.
if [ -n "$SINCE" ]; then
    SELECT="--since $SINCE"
else
    SELECT="--only-missing"
fi
python3 "$PMSCRAPER" --apply $SELECT --progress --report "$LOG" >>"$LOG" 2>&1

# Final summary toast (best-effort; some builds lack /notify).
SUMMARY=$(grep -E '^(scraped|unknown)' "$LOG" | tr '\n' ' ')
if [ -n "$SUMMARY" ]; then
    curl -s -m 3 -X POST --data "pmscraper: $SUMMARY" \
        "http://127.0.0.1:1234/notify" >/dev/null 2>&1 || true
fi

exit 0
