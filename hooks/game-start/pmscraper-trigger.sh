#!/bin/sh
# pmscraper game-start trigger for EmulationStation (KNULLI / Batocera).
#
# ES fires game-start with (es-app/src/FileData.cpp, launchGame):
#   Scripting::fireEvent("game-start", rom, basename, getName())
#     $1 = escaped rom path   e.g. /userdata/roms/ports/PortMaster.sh
#     $2 = basename (stem)     e.g. PortMaster        (no extension)
#     $3 = display name
#
# game-end, by contrast, fires with NO arguments, so it cannot tell what ran.
# We therefore detect PortMaster *here* and drop a marker the game-end hook
# consumes - that is the only reliable way to "scrape after PortMaster exits".
#
# Installed by install.sh to:
#   <ES configs>/scripts/game-start/pmscraper-trigger.sh

# Record the launch time; the game-end hook scrapes only ports whose port.json
# was written after this (i.e. installed during this PortMaster session).
case "$2" in
    PortMaster|PortMaster.sh)
        date +%s > /tmp/pmscraper.trigger
        ;;
esac
exit 0
