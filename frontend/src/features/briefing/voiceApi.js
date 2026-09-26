import { api } from '../../api'

// The presenter voice (backend/voice.py). The server only ever speaks text it wrote itself: a segment
// is asked for by the key the deck gave it.
//   GET  /api/voice/status   → {configured, provider, model, voices, languages, chars_left_today, attribution}
//   POST /api/voice/segment  → {key, audio_url, duration_s, words: [[w, t0, t1]], cues: [[name, value, t]], provider, role}
//                              503 'Voice not configured' · 429 quota · 409 'Script expired — fetch it again'
//   POST /api/voice/download → {mp3_url|null, vtt_url, txt_url, missing}

let statusP = null
export function getVoiceStatus() {
  if (!statusP) {
    statusP = api('/api/voice/status').catch(() => {
      statusP = null
      return { configured: false, provider: 'browser' }
    })
  }
  return statusP
}

const segments = new Map() // key -> Promise<segment>
export function getSegment(key) {
  if (!segments.has(key)) {
    const p = api('/api/voice/segment', { method: 'POST', body: { key } })
    p.catch(() => segments.get(key) === p && segments.delete(key))
    segments.set(key, p)
    while (segments.size > 200) segments.delete(segments.keys().next().value)
  }
  return segments.get(key)
}

export const getDownload = (deckKey, lang) => api('/api/voice/download', { method: 'POST', body: { deck_key: deckKey, lang } })
