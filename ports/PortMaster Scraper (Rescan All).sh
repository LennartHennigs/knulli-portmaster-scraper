#!/bin/bash
# PortMaster Scraper (Rescan All) - launchable from the KNULLI / Batocera Ports
# menu.
#
# Same as the "PortMaster Scraper" entry, but with --force: re-resolves and
# OVERWRITES every managed field (name/desc/image/thumbnail/genre/developer/
# publisher/releasedate/rating/players/...) on every installed port, not just
# ones that are missing data. Use this after updating pmscraper, or if a
# port's metadata looks stale from an older run. Installed by install.sh into
# <roms>/ports/.

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
LOG="$controlfolder/pmscraper-force.log"
REPORT="$controlfolder/pmscraper-force-report.md"
: > "$LOG"

toast() {   # ES on-screen notifier (best-effort; only shows once ES is foreground)
    curl -s -m 3 -X POST --data "$1" "http://127.0.0.1:1234/notify" >/dev/null 2>&1 || true
}

if [ ! -f "$PMSCRAPER" ]; then
    # control.txt is already sourced (get_controls ran gptokeyb), so hand back
    # to ES cleanly with pm_finish before bailing.
    toast "pmscraper.py not found - re-run install.sh"
    pm_finish
    exit 1
fi
$ESUDO chmod +x "$PMSCRAPER" 2>/dev/null || true

# Version for the on-screen title (the log carries it too, but nobody reads the
# log on the device). Read from the source rather than `--version` so we do not
# pay a python startup before the first frame; same one-liner make-release.sh
# and the installer use. Empty on a miss, and the label just drops it.
PMVER=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$PMSCRAPER" | head -1)
TITLE="PortMaster Scraper - Rescan All${PMVER:+ v$PMVER}"

# --- graphical progress via PortMaster's pugwash GUI ----------------------- #
# PortMasterDialogInit starts pugwash in fifo_control mode; PortMasterDialog
# sends it commands (message / progress / progress_clear). Always tear it down.
source "$controlfolder/PortMasterDialog.txt"
PortMasterDialogInit "no-harbour"
trap 'PortMasterDialogExit; pm_finish' EXIT

PortMasterDialog "messages_begin"
PortMasterDialog "message" "$TITLE\nre-scanning ALL ports - this overwrites existing data..."

# --emit-progress: PMPROG lines on stdout (one per port), human log on stderr.
# Drive the pugwash progress bar from each PMPROG line; log the rest.
python3 "$PMSCRAPER" --apply --online --force --emit-progress --report "$REPORT" 2>>"$LOG" |
while IFS="$(printf '\t')" read -r tag idx total name; do
    [ "$tag" = "PMPROG" ] || continue
    PortMasterDialog "progress" "$name  [$idx/$total]" "$idx" "$total"
done
RC=${PIPESTATUS[0]}   # pmscraper's exit, not the while loop's

PortMasterDialog "progress_clear"

if [ "$RC" -ge 2 ]; then
    # 0 = ok, 1 = ok but some launchers unidentified, >=2 = a real error.
    PortMasterDialog "message" "$TITLE\nScrape failed (exit $RC) - see $LOG"
    sleep 3
else
    # Summarise from the log (human output went to stderr -> $LOG).
    WRITTEN=$(grep -oE '[0-9]+ written' "$LOG" | head -1 | grep -oE '^[0-9]+')
    UNKNOWN=$(grep -oE '^unknown +[0-9]+' "$LOG" | head -1 | grep -oE '[0-9]+')
    PortMasterDialog "message" "$TITLE\nDone: ${WRITTEN:-0} updated, ${UNKNOWN:-0} unidentified."
    sleep 2
fi
PortMasterDialog "messages_end"

# trap runs PortMasterDialogExit + pm_finish on exit.
