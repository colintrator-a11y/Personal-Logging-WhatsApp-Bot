#!/usr/bin/env bash
# Is the bot running? ./status.sh
cd "$(dirname "$0")" || exit 1
# Read just the port. Sourcing .env would drag secrets into this shell and
# chokes on the multi-line-looking private key anyway.
PORT=$(sed -n 's/^BRAIN_PORT=//p' .env 2>/dev/null | head -1)
PORT="${PORT:-8000}"
ok=0

echo "── bridge (WhatsApp) ──────────────────────────────"
n=$(ps -eo args | grep -c "^node bridge/index\.js$")
if [ "$n" -eq 1 ]; then
  echo "  ✅ running (pid $(cat data/bridge.pid 2>/dev/null || echo '?'))"
elif [ "$n" -eq 0 ]; then
  echo "  ❌ NOT running    -> npm start"; ok=1
else
  echo "  ⚠️  $n instances running — they will fight over the session"
  echo "     -> pkill -f 'bridge/index.js' then start one"; ok=1
fi

if [ -f data/bridge.log ]; then
  drops=$(grep -c 'connection closed' data/bridge.log)
  last=$(grep -E 'connected as|connection closed|logged out|took over' data/bridge.log | tail -1)
  echo "  last event: ${last:-none}"
  if [ "$drops" -gt 5 ]; then
    echo "  ⚠️  $drops disconnects in this log — check for a second instance"
  else
    echo "  disconnects: $drops"
  fi
fi

echo
echo "── brain (Gemini + Sheets) ────────────────────────"
health=$(curl -s --max-time 3 "http://127.0.0.1:${PORT}/health" 2>/dev/null)
if [ -n "$health" ]; then
  echo "  ✅ responding on :$PORT"
  echo "     $health"
  case "$health" in *'"ok":false'*) echo "  ⚠️  config incomplete — see missing_config above"; ok=1;; esac
  case "$health" in *'"queued_writes":0'*) ;; *) echo "  ⚠️  writes are queued — Sheets may be failing";; esac
else
  echo "  ❌ no response on :$PORT   -> .venv/bin/python -m app.main"; ok=1
fi

echo
echo "── recent activity ────────────────────────────────"
if [ -s data/bot.jsonl ]; then
  tail -5 data/bot.jsonl | .venv/bin/python -c '
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    print("  ", d["at"][:19], d["event"], repr(d.get("text",""))[:44])
' 2>/dev/null || tail -3 data/bot.jsonl
else
  echo "   no messages processed yet"
fi

echo
[ "$ok" -eq 0 ] && echo "All good. Send yourself a message to test." || echo "Something needs attention — see ❌ / ⚠️  above."
exit $ok
