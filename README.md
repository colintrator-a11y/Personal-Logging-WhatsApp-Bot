# Personal expense log bot

You message yourself on WhatsApp, it lands in a Google Sheet.

Two processes:

- **`bridge/`** (Node) — Baileys client. Owns the WhatsApp session, filters
  messages, forwards them over localhost, sends the reply back.
- **`app/`** (Python) — the brain. Gemini parses the text, the row goes to
  Sheets, the reply text comes back.

The bridge holds no logic and the brain knows nothing about WhatsApp, so you can
`curl` the brain to test everything without touching your phone.

```
you ──WhatsApp──▶ bridge/index.js ──POST /message──▶ app/main.py ──▶ Gemini
                        ▲                                  │
                        └──────── reply text ──────────────┴──▶ Google Sheets
```

## Setup

### 1. Dependencies

```bash
npm install
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### 2. Google Sheet

Create a sheet with a tab named `Log`. The header row is written automatically
on first start:

`timestamp | date | amount | currency | category | description | raw_message`

Then in Google Cloud: enable the **Google Sheets API**, create a **service
account**, download its JSON key, and **share the sheet with the service
account's email as Editor**. That last step is the one people forget — without
it every write returns 403.

### 3. Gemini key

From <https://aistudio.google.com/apikey>. The free tier covers this easily.

The default is `gemini-3.5-flash-lite` — a parse takes about a second. Note that
`gemini-2.5-flash-lite` returns 404 for keys created after its retirement, so if
you see `no longer available to new users`, list what your key can actually
reach and set `GEMINI_MODEL` to one of those:

```bash
.venv/bin/python -c "
from app import config
from google import genai
for m in genai.Client(api_key=config.GEMINI_API_KEY).models.list():
    if 'generateContent' in (m.supported_actions or []): print(m.name)
"
```

### 4. `.env`

```bash
cp .env.example .env
openssl rand -hex 24   # paste into BRIDGE_TOKEN
```

Fill in `GEMINI_API_KEY`, `SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_EMAIL` and
`GOOGLE_PRIVATE_KEY` (one line, keep the literal `\n` sequences), then set `TZ`
and `DEFAULT_CURRENCY` to yours.

### 5. Run

```bash
.venv/bin/python -m app.main     # terminal 1
npm start                        # terminal 2 — prints a QR
```

Scan the QR from **WhatsApp → Linked devices**. The session persists to `auth/`,
so restarts do not need a new scan.

> Use a spare number. Baileys drives WhatsApp Web unofficially; the ban risk for
> personal-volume use is low but not zero.

Check the brain on its own at any time:

```bash
curl localhost:8000/health
curl -XPOST localhost:8000/message -H 'content-type: application/json' \
  -H "x-bridge-token: $BRIDGE_TOKEN" \
  -d '{"message_id":"1","chat_jid":"test","text":"almuerzo 1200"}'
```

### 6. Keep it running

```bash
npm i -g pm2
pm2 start ecosystem.config.cjs
pm2 save && pm2 startup
```

## Start / stop

```bash
./start.sh    # brings up both processes, waits until each is actually ready
./stop.sh     # stops both, keeps the WhatsApp session
./status.sh   # is it healthy?
```

`start.sh` is idempotent — running it twice will not spawn a second bridge.
`stop.sh` leaves `auth/` alone, so starting again needs no new QR scan.

## Is it running?

```bash
./status.sh
```

Checks both processes, the session state, queued writes and recent activity,
and tells you the command to fix whatever is down.

**Run exactly one bridge.** Two instances kick each other off the WhatsApp
session every few seconds (`connection closed 440`) and neither stays up long
enough to read a message. The bridge now refuses to start if another one holds
`data/bridge.pid`, and exits rather than fighting back if it gets replaced.

## Using it

Message **yourself** (WhatsApp → search your own name → "Message yourself").
Anything you type in another chat is ignored unless you list that chat's JID in
`ALLOWED_CHAT_JIDS`; messages from other people are never accepted, anywhere.

```
almuerzo 1200            →  ✅ ¥1,200 · food · 26 Aug
taxi 3500 ayer           →  ✅ ¥3,500 · transport · 25 Aug
food 40 and parking 12   →  ✅ ¥40 · food · 26 Aug
                            ✅ ¥12 · transport · 26 Aug
coffee                   →  ❓ How much was the coffee?
480                      →  ✅ ¥480 · food · 26 Aug
```

Relative dates work in any language — "yesterday", "ayer", "昨日" — because the
model is handed today's date and returns an absolute one.

| Command | Does |
|---|---|
| `undo` | Removes the rows the last logged message produced |
| `total` | This month, with a per-category breakdown |
| `total food` | This month, one category |
| `clean` | Deletes every data row, keeps the header |
| `help` | The above, plus the categories in use |

`clean` writes `data/sheet-backup-<timestamp>.json` before deleting, and the
sheet's own File → Version history is a second net.

## Categories

Learned from the sheet, not fixed. Before each parse the bot reads the distinct
values in the `categoría` column and hands them to the model, which reuses one
when it fits and coins a new one when nothing does:

```
lunch 12            -> food        (reused)
rent 900            -> rent        (new)
vet for the dog 65  -> pets        (new)
```

`SEED_CATEGORIES` in `app/config.py` only bootstraps an empty sheet.

The risk of a learned list is drift — `food`, `Food` and `comida` splitting one
category three ways. `gemini._clean_category` limits it: the model is told not
to coin near-duplicates, new names are lowercased and trimmed, anything already
present is snapped to its existing spelling, and anything longer than two words
falls back to `other`. It reduces drift; it does not eliminate it. If your
totals start fragmenting, edit the column in the sheet and the bot follows —
the sheet is the source of truth.

The list is cached for `CATEGORY_CACHE_SECONDS` (60 by default) and invalidated
on every write, so a new category is visible to the next message.

## When things fail

| Failure | What happens |
|---|---|
| Gemini 429 | Retries at 1s, 2s, 4s, 8s. Bad requests fail immediately instead of burning the backoff |
| Sheets write fails | 3 tries, then the rows go to `data/queue.jsonl` and a flusher retries every `QUEUE_FLUSH_MS`. **No ✅ is sent** — the reply says it is queued |
| Brain down | The bridge replies that the parser is down; nothing is logged |
| Anything else | Caught in `/message`; the process stays up |

Every message, parse result and write lands in `data/bot.jsonl`, one JSON object
per line. When a parse looks wrong, that file plus the `raw_message` column in
the sheet is enough to reconstruct what happened:

```bash
tail -f data/bot.jsonl | jq .
```

## Out of scope

Charts, dashboards, receipt photos, voice notes, budgets, recurring expenses,
currency conversion. Totals are reported per currency rather than converted.

## Layout

```
bridge/index.js      Baileys socket, message filter, reply sender
app/main.py          FastAPI endpoint + queue flusher
app/handlers.py      command routing, the log-an-expense flow
app/gemini.py        prompt, response schema, backoff, validation
app/sheets.py        append / read / delete via a service account
app/store.py         jsonl log, retry queue, undo pointer, pending asks
app/fmt_helpers.py   money and date formatting for replies
app/config.py        env loading, the category list
```
