import { api } from '../../api'

// The presenter voice (backend/voice.py). The server only ever speaks text it wrote itself: a segment
// is asked for by the key the deck gave it.
//   GET  /api/voice/status   → {configured, provider, model, voices, speakers, languages, chars_left_today, attribution}
//   POST /api/voice/segment  → {key, audio_url, duration_s, words: [[w, t0, t1]], cues: [[name, value, t]], provider, role, speaker}
//                              503 'Voice not configured' · 429 quota · 409 'Script expired — fetch it again'
//   POST /api/voice/download → {mp3_url|null, vtt_url, txt_url, missing}

// Asked once per page. The server looks the voices' names up in the background and answers without them at first:
// an answer that is configured but still unnamed is asked again on the next call (at most every 4 s).
let statusP = null
let statusAt = 0
const unnamed = (s) => s?.configured && !(s.speakers?.presenter && s.speakers?.analyst)
export function getVoiceStatus() {
  if (statusP && statusP.settled && unnamed(statusP.settled) && Date.now() - statusAt > 4000) statusP = null
  if (!statusP) {
    statusAt = Date.now()
    const p = api('/api/voice/status')
      .catch(() => {
        statusP = null
        return { configured: false, provider: 'browser' }
      })
      .then((s) => {
        p.settled = s
        return s
      })
    statusP = p
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

// `describe`: the viewer has the map descriptions on, so the files carry them (each beat's "On the map" line first)
export const getDownload = (deckKey, lang, describe = false) =>
  api('/api/voice/download', { method: 'POST', body: { deck_key: deckKey, lang, ...(describe ? { describe: true } : {}) } })
