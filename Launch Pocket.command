#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ -x /opt/homebrew/bin/python3 ]]; then
  pocket_python=/opt/homebrew/bin/python3
else
  pocket_python=$(command -v python3)
fi
if "$pocket_python" -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8768/api/state",timeout=2)); assert d["schemaVersion"]==1 and d["currency"]=="INR"' >/dev/null 2>&1; then
  open 'http://127.0.0.1:8768'
  exit 0
fi
"$pocket_python" server.py &
pocket_pid=$!
trap 'kill "$pocket_pid" 2>/dev/null || true' EXIT INT TERM
for pocket_attempt in {1..30}; do
  if "$pocket_python" -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8768/api/state",timeout=1)' >/dev/null 2>&1; then
    open 'http://127.0.0.1:8768'
    print 'Pocket is open. Keep this window open while using it. Your entries stay saved when you close it.'
    wait "$pocket_pid"
    exit 0
  fi
  sleep 0.2
done
print 'Pocket could not start. Check the message above, or ask LifeOS for help.'
exit 1
