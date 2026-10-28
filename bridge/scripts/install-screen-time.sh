#!/bin/sh
# Build the pinned reader and register its independent macOS privacy identity.
set -eu
test "$(uname -s)" = Darwin || { echo "Screen Time imports require macOS." >&2; exit 1; }
command -v swiftc >/dev/null || { echo "Install Xcode Command Line Tools: xcode-select --install" >&2; exit 1; }
revision=27fb07e3edb0f715c1e187fc41e6cb0458d63ab1
destination="$HOME/Library/Application Support/ScreenTimeArchive"
if [ ! -x "$destination/bin/screentime-archiver" ] || [ "$(cat "$destination/reader-revision" 2>/dev/null || true)" != "$revision" ]; then
source_dir=$(mktemp -d)
trap 'rm -rf "$source_dir"' EXIT HUP INT TERM
git -C "$source_dir" init -q
git -C "$source_dir" remote add origin https://github.com/tobias-x/screentime-archive.git
git -C "$source_dir" fetch -q --depth=1 origin "$revision"
git -C "$source_dir" checkout -q --detach FETCH_HEAD
# Reuse upstream's compiler flags and SDK workaround without building its unrelated menu bar app.
awk '/^echo "Building ScreenTimeBar.app/ { exit } { print }' "$source_dir/build.sh" > "$source_dir/build-reader.sh"
zsh "$source_dir/build-reader.sh"
mkdir -p "$destination/bin"
cp "$source_dir/LICENSE" "$destination/READER-LICENSE"
if ! cmp -s "$source_dir/build/screentime-archiver" "$destination/bin/screentime-archiver"; then
    cp "$source_dir/build/screentime-archiver" "$destination/bin/screentime-archiver"
    chmod 755 "$destination/bin/screentime-archiver"
    echo "Reader installed. Add it to Full Disk Access (remove/re-add an older entry after rebuilding):"
else
    echo "Reader is already current. Check its Full Disk Access permission:"
fi
echo "$revision" > "$destination/reader-revision"
fi
# plistlib escapes paths safely, including spaces in Library/Application Support.
python3 - <<'PY'
import plistlib
from pathlib import Path
home = Path.home()
folder = home / 'Library/Application Support/ScreenTimeArchive'
agents = home / 'Library/LaunchAgents'
agents.mkdir(parents=True, exist_ok=True)
with (agents / 'com.boredommaxxing.screentime.plist').open('wb') as file:
    plistlib.dump({'Label': 'com.boredommaxxing.screentime',
                  'ProgramArguments': [str(folder / 'bin/screentime-archiver')],
                  'RunAtLoad': True, 'StartInterval': 1800,
                  'StandardOutPath': str(folder / 'reader.log'),
                  'StandardErrorPath': str(folder / 'reader.log'),
                  'ProcessType': 'Background'}, file)
PY
if ! launchctl print "gui/$(id -u)/com.boredommaxxing.screentime" >/dev/null 2>&1; then
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.boredommaxxing.screentime.plist"
fi
echo "$destination/bin/screentime-archiver"
echo "Enable Share Across Devices on iPhone and Mac, then open Progress and tap Sync Mac."
