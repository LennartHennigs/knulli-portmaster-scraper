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

# Mirror output to the on-screen console (CUR_TTY) and to a log file.
> "$LOG"
exec > >(tee "$LOG" "$CUR_TTY") 2>&1

if [ ! -f "$PMSCRAPER" ]; then
    echo "pmscraper.py not found at $PMSCRAPER - re-run install.sh"
    sleep 5
    pm_finish
    exit 1
fi

$ESUDO chmod +x "$PMSCRAPER" 2>/dev/null || true

echo "Scraping installed ports..."
echo "(first --online run downloads the catalog; give it a moment)"
echo

# --online also covers hand-installed ports and any missing artwork cache.
python3 "$PMSCRAPER" --apply --online --report "$controlfolder/pmscraper-report.md"

echo
echo "Done. Report: $controlfolder/pmscraper-report.md"
sleep 4

pm_finish
