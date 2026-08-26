import 'dotenv/config'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { Boom } from '@hapi/boom'
import pino from 'pino'
import qrcode from 'qrcode-terminal'
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  jidNormalizedUser,
  useMultiFileAuthState,
  Browsers,
} from 'baileys'

const BRAIN_URL = process.env.BRAIN_URL || 'http://127.0.0.1:8000'
const BRIDGE_TOKEN = process.env.BRIDGE_TOKEN || ''
const AUTH_DIR = process.env.AUTH_DIR || './auth'
const BRAIN_TIMEOUT_MS = Number(process.env.BRAIN_TIMEOUT_MS || 20000)

// Empty = self-chat only. Anything listed here can also drive the bot,
// but only for messages *you* sent (fromMe is enforced unconditionally).
const ALLOWED = (process.env.ALLOWED_CHAT_JIDS || '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)

const logger = pino({ level: process.env.BAILEYS_LOG_LEVEL || 'silent' })

// Baileys re-delivers on reconnect, so this has to survive a restart —
// otherwise every `pm2 restart` re-logs whatever is still in the sync window.
const SEEN_FILE = process.env.SEEN_FILE || './data/seen.json'
let seen = new Set()
try {
  seen = new Set(JSON.parse(readFileSync(SEEN_FILE, 'utf8')))
} catch {
  // no file yet, or it is corrupt — starting empty is correct either way
}
const remember = (id) => {
  seen.add(id)
  while (seen.size > 1000) seen.delete(seen.values().next().value)
  try {
    mkdirSync(dirname(SEEN_FILE), { recursive: true })
    writeFileSync(SEEN_FILE, JSON.stringify([...seen]))
  } catch (err) {
    say('could not persist seen ids:', err?.message || err)
  }
}

const stamp = () => new Date().toISOString()
const say = (...a) => console.log(stamp(), ...a)

function extractText(msg) {
  const m = msg.message
  if (!m) return null
  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    m.ephemeralMessage?.message?.conversation ||
    m.ephemeralMessage?.message?.extendedTextMessage?.text ||
    null
  )
}

async function askBrain(payload) {
  const res = await fetch(`${BRAIN_URL}/message`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-bridge-token': BRIDGE_TOKEN,
    },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(BRAIN_TIMEOUT_MS),
  })
  if (!res.ok) throw new Error(`brain ${res.status}: ${(await res.text()).slice(0, 200)}`)
  return res.json()
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
  const { version } = await fetchLatestBaileysVersion()

  const sock = makeWASocket({
    version,
    auth: state,
    logger,
    browser: Browsers.ubuntu('Chrome'),
    syncFullHistory: false,
    markOnlineOnConnect: false, // keep phone notifications working
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', (u) => {
    const { connection, lastDisconnect, qr } = u
    if (qr) {
      say('scan this QR with WhatsApp > Linked devices')
      qrcode.generate(qr, { small: true })
    }
    if (connection === 'open') {
      say('connected as', jidNormalizedUser(sock.user?.id))
      say(ALLOWED.length ? `extra chats allowed: ${ALLOWED.join(', ')}` : 'self-chat only')
    }
    if (connection === 'close') {
      const code = new Boom(lastDisconnect?.error)?.output?.statusCode
      if (code === DisconnectReason.loggedOut) {
        say('logged out — delete', AUTH_DIR, 'and re-scan the QR')
        process.exit(1)
      }
      say('connection closed', code ?? '', '— reconnecting in 3s')
      setTimeout(start, 3000)
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    // 'notify' is a live message; 'append' is one you typed on another linked
    // device (phone, WhatsApp Web) that got synced to us. We want both.
    if (type !== 'notify' && type !== 'append') return
    for (const msg of messages) {
      try {
        await handle(sock, msg)
      } catch (err) {
        say('handler error:', err?.message || err)
      }
    }
  })
}

async function handle(sock, msg) {
  const id = msg.key?.id
  const chatJid = msg.key?.remoteJid
  if (!id || !chatJid || seen.has(id)) return

  // Only messages you typed yourself, ever.
  if (!msg.key.fromMe) return

  const me = jidNormalizedUser(sock.user?.id)
  const chat = jidNormalizedUser(chatJid)
  // Self-chat can arrive under either your phone-number jid or your lid.
  const isSelfChat =
    chat === me || (sock.user?.lid && chat === jidNormalizedUser(sock.user.lid))
  if (!isSelfChat && !ALLOWED.includes(chat)) {
    say('ignored: chat', chat, 'is not your self-chat and is not in ALLOWED_CHAT_JIDS')
    return
  }

  const text = extractText(msg)?.trim()
  if (!text) return

  remember(id)
  say('<<', text)

  let reply
  try {
    const out = await askBrain({
      message_id: id,
      chat_jid: chatJid,
      text,
      ts: Number(msg.messageTimestamp) || Math.floor(Date.now() / 1000),
    })
    reply = out?.reply
  } catch (err) {
    say('brain unreachable:', err?.message || err)
    reply = '⚠️ Parser is down. Message not logged — resend it once it is back up.'
  }

  if (!reply) return
  await sock.sendMessage(chatJid, { text: reply })
  say('>>', reply.replace(/\n/g, ' | '))
}

process.on('unhandledRejection', (e) => say('unhandledRejection:', e?.message || e))
process.on('uncaughtException', (e) => say('uncaughtException:', e?.message || e))

start().catch((e) => {
  say('fatal:', e?.message || e)
  process.exit(1)
})
