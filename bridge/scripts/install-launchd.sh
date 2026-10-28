#!/bin/sh
# Copies the plists into ~/Library/LaunchAgents. Does NOT load them; prints the commands instead.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
dest="$HOME/Library/LaunchAgents"
mkdir -p "$here/data/logs" "$dest"
for name in bridge evening; do
  cp "$here/launchd/com.boredommaxxing.$name.plist" "$dest/"
done
echo "Installed plists to $dest. To start them, run:"
for name in bridge evening; do
  echo "  launchctl bootstrap gui/$(id -u) $dest/com.boredommaxxing.$name.plist"
done
echo "To stop: launchctl bootout gui/$(id -u)/com.boredommaxxing.<bridge|evening>"
