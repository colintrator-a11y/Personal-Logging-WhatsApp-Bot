#!/usr/bin/env bash
# Stop both halves of the bot.  ./stop.sh
cd "$(dirname "$0")" || exit 1

stop_matching() {           # $1 = label, $2 = exact-args pattern for pkill -f
  local label="$1" pat="$2"
  if ! pgrep -f "$pat" >/dev/null 2>&1; then
    echo "$label  not running"
    return
  fi
  echo -n "$label  stopping..."
  pkill -f "$pat"
  for _ in $(seq 1 20); do
    pgrep -f "$pat" >/dev/null 2>&1 || break
    sleep 0.5
  done
  if pgrep -f "$pat" >/dev/null 2>&1; then
    pkill -9 -f "$pat"; sleep 1
    echo -n " (forced)"
  fi
  pgrep -f "$pat" >/dev/null 2>&1 && echo " ❌ still up" || echo " ✅"
}

stop_matching "bridge" "node bridge/index\.js"
stop_matching "brain " "python -m app\.main"

rm -f data/bridge.pid data/brain.pid

# The WhatsApp session in auth/ is deliberately left alone — deleting it would
# force a new QR scan on the next start.
echo
echo "Stopped. Session kept in auth/, so ./start.sh needs no new QR."
