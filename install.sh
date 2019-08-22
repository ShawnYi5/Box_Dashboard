#!/bin/sh

fromPath="BoxDashboard.service"
toPath="/usr/lib/systemd/system/BoxDashboard.service"

chmod 754 "$fromPath"
rm -f "$toPath"
cp "$fromPath" "$toPath"

logFolder="/var/log/aio"
if [ ! -d "$logFolder" ]; then
  mkdir -p "$logFolder"
fi

sudo systemctl daemon-reload
sudo systemctl enable BoxDashboard
