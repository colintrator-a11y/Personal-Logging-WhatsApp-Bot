#!/usr/bin/env bash
# Print the newest WhatsApp QR. Re-run until you manage to scan it.
cd "$(dirname "$0")" || exit 1

if ! ps -eo args | grep -q "^node bridge/index\.js$"; then
  echo "bridge is not running -> ./start.sh"; exit 1
fi
if [ ! -f data/bridge.log ] || ! grep -q 'scan this QR' data/bridge.log; then
  echo "no QR yet. If it says 'connected as', you are already linked;"
  echo "to link a different phone: ./stop.sh && rm -rf auth && ./start.sh"
  exit 1
fi
if grep -q 'connected as' data/bridge.log; then
  echo "✅ already linked as $(grep 'connected as' data/bridge.log | tail -1 | sed 's/.*connected as //')"
  exit 0
fi

age=$(( $(date +%s) - $(date -d "$(grep 'scan this QR' data/bridge.log | tail -1 | awk '{print $1}')" +%s) ))
awk '/scan this QR/{buf=""; next} {buf=buf $0 "\n"} END{printf "%s", buf}' data/bridge.log
echo "this QR is ${age}s old — they expire after about 20s, so re-run if it fails"
