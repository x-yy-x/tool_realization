#!/bin/sh
set -e
genconfig
echo "config ok; ninja # text, not a command"
timeout --signal=TERM 500 hb build
env BUILD_MODE=release nice -n 5 ninja
"$OPTIONAL_TOOL" --version
