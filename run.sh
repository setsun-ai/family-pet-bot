#!/usr/bin/env sh
# family-pet-bot: start on macOS / Linux / Raspberry Pi (first run = setup).
#   sh run.sh            start
#   sh run.sh --setup    setup wizard only
#   sh run.sh check      test connections and exit
set -eu
cd "$(dirname "$0")"
export PYTHONUTF8=1
exec python3 launcher.py "$@"
