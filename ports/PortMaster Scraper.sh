#!/bin/bash
# PortMaster Scraper - launchable from the KNULLI / Batocera Ports menu.
#
# Scrapes the WHOLE ports folder (every installed port) and writes metadata +
# artwork into the gamelist. Shows a native on-screen progress bar by driving
# PortMaster's own GUI (pugwash) over its dialog FIFO - the same UI PortMaster
# uses when it installs a port. Installed by install.sh into <roms>/ports/.
#
# (The auto-run game-end hook is the "new ports only" path; this entry is the
# full rescan you run by hand.)

XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}

if [ -d "/opt/system/Tools/PortMaster/" ]; then
  controlfolder="/opt/system/Tools/PortMaster"
elif [ -d "/opt/tools/PortMaster/" ]; then
  controlfolder="/opt/tools/PortMaster"
elif [ -d "$XDG_DATA_HOME/PortMaster/" ]; then
  controlfolder="$XDG_DATA_HOME/PortMaster"
else
  controlfolder="/roms/ports/PortMaster"
fi

source "$controlfolder/control.txt"
get_controls
[ -f "${controlfolder}/mod_${CFW_NAME}.txt" ] && source "${controlfolder}/mod_${CFW_NAME}.txt"

PMSCRAPER="$controlfolder/pmscraper/pmscraper.py"
LOG="$controlfolder/pmscraper.log"
REPORT="$controlfolder/pmscraper-report.md"
: > "$LOG"

toast() {   # ES on-screen notifier (best-effort; only shows once ES is foreground)
    curl -s -m 3 -X POST --data "$1" "http://127.0.0.1:1234/notify" >/dev/null 2>&1 || true
}

if [ ! -f "$PMSCRAPER" ]; then
    toast "pmscraper.py not found - re-run install.sh"
    exit 1
fi
$ESUDO chmod +x "$PMSCRAPER" 2>/dev/null || true

# --- graphical progress via PortMaster's pugwash GUI ----------------------- #
# PortMasterDialogInit starts pugwash in fifo_control mode; PortMasterDialog
# sends it commands (message / progress / progress_clear). Always tear it down.
source "$controlfolder/PortMasterDialog.txt"
PortMasterDialogInit "no-harbour"
trap 'PortMasterDialogExit; pm_finish' EXIT

PortMasterDialog "messages_begin"
PortMasterDialog "message" "PortMaster Scraper\nscanning installed ports..."

# --emit-progress: PMPROG lines on stdout (one per port), human log on stderr.
# Drive the pugwash progress bar from each PMPROG line; log the rest.
python3 "$PMSCRAPER" --apply --online --emit-progress --report "$REPORT" 2>>"$LOG" |
while IFS="$(printf '\t')" read -r tag done total name; do
    [ "$tag" = "PMPROG" ] || continue
    PortMasterDialog "progress" "$name  [$done/$total]" "$done" "$total"
done

PortMasterDialog "progress_clear"

# Summarise from the log (human output went to stderr -> $LOG).
WRITTEN=$(grep -oE '[0-9]+ written' "$LOG" | head -1 | grep -oE '^[0-9]+')
UNKNOWN=$(grep -oE '^unknown +[0-9]+' "$LOG" | head -1 | grep -oE '[0-9]+')
PortMasterDialog "message" "Done: ${WRITTEN:-0} updated, ${UNKNOWN:-0} unidentified."
PortMasterDialog "message" "Reloading gamelist..."
sleep 2
PortMasterDialog "messages_end"

# trap runs PortMasterDialogExit + pm_finish on exit.
