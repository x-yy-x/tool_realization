#!/bin/sh
if [ -d prebuilts ]; then
  echo "prebuilts found"
fi
sudo -n env CHECK=1 timeout 2m hb env
