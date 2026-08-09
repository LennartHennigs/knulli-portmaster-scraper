#!/bin/bash
# PortMaster Scraper - launchable from the KNULLI / Batocera Ports menu.
#
# Transcribes PortMaster's own metadata + artwork into the ports gamelist, then
# reloads EmulationStation so the menu refreshes in place. Uses the standard
# PortMaster port skeleton so it runs in the right environment on every CFW.
# Installed by install.sh into <roms>/ports/ so ES lists it.

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

# Log to a file, and best-effort to the console (some devices show the port's
# tty, most don't - we rely on the /notify toast below for on-screen feedback).
: > "$LOG"
if [ -n "${CUR_TTY:-}" ] && [ -w "${CUR_TTY:-/dev/null}" ]; then
    exec > >(tee "$LOG" "$CUR_TTY") 2>&1
else
    exec > "$LOG" 2>&1
fi

toast() {   # POST a short message to the ES on-screen notifier (best-effort)
    curl -s -m 3 -X POST --data "$1" "http://127.0.0.1:1234/notify" >/dev/null 2>&1 || true
}

if [ ! -f "$PMSCRAPER" ]; then
    toast "pmscraper.py not found - re-run install.sh"
    pm_finish
    exit 1
fi

$ESUDO chmod +x "$PMSCRAPER" 2>/dev/null || true

# Offline by default: everything installed via PortMaster has its metadata and
# (via the images_pm cache) its artwork on the card already, so no network is
# needed - which matters because handhelds are often offline. pmscraper reloads
# ES itself when it changes anything. For the online top-up (hand-installed
# ports, uncached covers) run it over SSH with --online while on WiFi.
echo "Scraping installed ports..."
python3 "$PMSCRAPER" --apply --report "$REPORT"

# Toast the summary so there's feedback on the handheld even with no console.
SUMMARY=$(grep -E '^(scraped|unknown|stale)' "$LOG" | tr '\n' ' ')
toast "pmscraper: ${SUMMARY:-see $LOG}"

pm_finish
