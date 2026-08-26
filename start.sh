#!/usr/bin/env bash
# Start both halves of the bot.  ./start.sh
cd "$(dirname "$0")" || exit 1
mkdir -p data

PORT=$(sed -n 's/^BRAIN_PORT=//p' .env 2>/dev/null | head -1); PORT="${PORT:-8000}"
BRAIN_PID_FILE=data/brain.pid

alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }
bridges() { ps -eo args | grep -c "^node bridge/index\.js$"; }

if [ ! -f .env ]; then
  echo "❌ no .env — copy .env.example and fill it in"; exit 1
fi

# ---- brain ----------------------------------------------------------------
if curl -s --max-time 3 -o /dev/null "http://127.0.0.1:${PORT}/health"; then
  echo "brain   already running on :$PORT"
else
  echo -n "brain   starting..."
  nohup .venv/bin/python -m app.main >> data/brain.log 2>&1 &
  echo $! > "$BRAIN_PID_FILE"
  for _ in $(seq 1 60); do
    curl -s --max-time 1 -o /dev/null "http://127.0.0.1:${PORT}/health" && break
    sleep 0.5
  done
  if curl -s --max-time 3 -o /dev/null "http://127.0.0.1:${PORT}/health"; then
    echo " ✅ :$PORT"
  else
    echo " ❌ failed — last lines of data/brain.log:"
    tail -15 data/brain.log; exit 1
  fi
fi

# ---- bridge ---------------------------------------------------------------
# Exactly one, always. Two fight over the WhatsApp session and neither survives.
n=$(bridges)
if [ "$n" -gt 1 ]; then
  echo "bridge  ⚠️  $n instances already running — stopping all first"
  ./stop.sh >/dev/null 2>&1
  n=0
fi

if [ "$n" -eq 1 ]; then
  echo "bridge  already running (pid $(cat data/bridge.pid 2>/dev/null || echo '?'))"
else
  echo -n "bridge  starting..."
  # Only look at lines written after this point — the log is appended to, so an
  # old "connected as" from a previous run would otherwise satisfy the wait.
  before=$( [ -f data/bridge.log ] && wc -l < data/bridge.log || echo 0 )
  nohup node bridge/index.js >> data/bridge.log 2>&1 &
  for _ in $(seq 1 40); do
    tail -n "+$((before + 1))" data/bridge.log 2>/dev/null \
      | grep -qE 'connected as|already running|logged out|took over' && break
    sleep 0.5
  done
  new_lines=$(tail -n "+$((before + 1))" data/bridge.log 2>/dev/null)
  if [ "$(bridges)" -ge 1 ] && printf '%s' "$new_lines" | grep -q 'connected as'; then
    echo " ✅ $(printf '%s' "$new_lines" | grep 'connected as' | tail -1 | sed 's/.*connected as //')"
  else
    echo " ❌ failed:"
    printf '%s\n' "$new_lines" | tail -15; exit 1
  fi
fi

echo
echo "Running. Send yourself a message, or watch:  tail -f data/bridge.log"
echo "Check anytime: ./status.sh    Stop: ./stop.sh"
