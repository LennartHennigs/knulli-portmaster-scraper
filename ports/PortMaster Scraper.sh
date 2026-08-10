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

# --online so cover/box art (which the local images_pm cache usually lacks) is
# pulled from the PortMaster repo when WiFi is up. It degrades gracefully with no
# network - screenshots + metadata still come from the on-card cache offline.
# pmscraper reloads ES itself when it changes anything.
echo "Scraping installed ports..."
python3 "$PMSCRAPER" --apply --online --report "$REPORT"

# One concise toast: how many entries changed + how many unidentified. Launched
# from the Ports menu ES is backgrounded, so its live gamelist reload may not
# repaint until you leave the menu - hence the reminder.
WRITTEN=$(grep -oE '[0-9]+ written' "$LOG" | head -1 | grep -oE '^[0-9]+')
UNKNOWN=$(grep -oE '^unknown +[0-9]+' "$LOG" | head -1 | grep -oE '[0-9]+')
toast "PM Scraper: ${WRITTEN:-0} updated, ${UNKNOWN:-0} unidentified"

pm_finish
