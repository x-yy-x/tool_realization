#!/bin/bash
set -e
./build/genconfig.sh
if [ -f "$OUT/config" ]; then
  echo "config ok"
fi
timeout 500 hb build -f
