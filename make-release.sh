#!/bin/sh
# make-release.sh - build the distributable installer + release zip.
#
# Produces:
#   build/Install PortMaster Scraper.sh                  (self-extracting, no-SSH)
#   build/knulli-portmaster-scraper-v<VERSION>.zip       (installer + README)
#
# The .sh = installer/installer-stub.sh + a payload marker + a gzipped tarball of
# everything install.sh needs beside itself. Drop the .sh into <roms>/ports/ and
# run it from the Ports menu. Nothing here is committed; build/ is gitignored.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' pmscraper.py)
[ -n "$VERSION" ] || { echo "could not read VERSION from pmscraper.py" >&2; exit 1; }

INSTALLER="Install PortMaster Scraper.sh"
ZIP="knulli-portmaster-scraper-v$VERSION.zip"

echo "building v$VERSION"
rm -rf build
mkdir -p build

# --- stage the payload exactly as install.sh expects beside itself --------- #
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/ports" "$STAGE/hooks/game-start" "$STAGE/hooks/game-end"
cp pmscraper.py install.sh                       "$STAGE/"
cp "ports/PortMaster Scraper.sh"                 "$STAGE/ports/"
cp "ports/PortMaster Scraper (Rescan All).sh"    "$STAGE/ports/"
cp hooks/game-start/pmscraper-trigger.sh         "$STAGE/hooks/game-start/"
cp hooks/game-end/pmscraper-hook.sh              "$STAGE/hooks/game-end/"

# --- self-extracting installer = stub + marker + tarball ------------------- #
cp installer/installer-stub.sh "build/$INSTALLER"
printf '__PMSCRAPER_PAYLOAD__\n' >> "build/$INSTALLER"
tar czf - -C "$STAGE" . >> "build/$INSTALLER"
chmod 755 "build/$INSTALLER"

# --- release zip (installer + README) -------------------------------------- #
cp README.md build/README.md
( cd build && zip -q "$ZIP" "$INSTALLER" README.md && rm -f README.md )

echo "  build/$INSTALLER"
echo "  build/$ZIP"
echo "done"
