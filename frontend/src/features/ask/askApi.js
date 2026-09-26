import { api, assetUrl } from '../../api'

// Ask Overload (backend/ask.py): one question about the scenario on screen, answered from the
// engine's fact sheet. `caseBody` is a case (grid.py → CaseIn, plus an optional catastrophe `preset`).
//   askQuestion → {answer, cited:[{key,label,text}], tool:{name,args,label}|null, tool_calls, source:
//                  'gemini'|'pattern', fallback, declined, numbers_checked, voice_key, report_key, lang}
//   askSuggestions → {questions: [4 strings]} (no AI)
export const askQuestion = (caseBody, question, lang = 'en') =>
  api('/api/ask', { method: 'POST', body: { case: caseBody, question, lang } })
export const askSuggestions = (caseBody, lang = 'en') => api('/api/ask/suggestions', { method: 'POST', body: { case: caseBody, lang } })

// The voice service (backend/voice.py): configured or not, once per page load. A missing or
// unconfigured service means the browser's own voice reads the answer.
let voiceReady = null
export function voiceConfigured() {
  if (!voiceReady) {
    voiceReady = api('/api/voice/status')
      .then((s) => !!s?.configured)
      .catch(() => false)
  }
  return voiceReady
}
// → {audio_url, duration_s, words, provider}; 503 when not configured, 429 when the day's quota is used
export const voiceSegment = (key) => api('/api/voice/segment', { method: 'POST', body: { key } })
export const audioUrl = (path) => assetUrl(path)

// The case a finished cascade actually ran with: the store's case, with the fields the cascade echoes
// back taken from the response (a mode may override them for one run, e.g. a storm's trip list).
export function caseForCascade(cascade, caseBody) {
  if (!cascade) return caseBody || null
  const body = {
    ...caseBody,
    region: cascade.region ?? caseBody?.region,
    load_factor: cascade.load_factor ?? caseBody?.load_factor,
    trip: cascade.trip ?? caseBody?.trip,
    upgrades: cascade.upgrades ?? caseBody?.upgrades,
  }
  const count = (caseBody?.lat != null ? 1 : 0) + (caseBody?.sites?.length || 0)
  if (Array.isArray(cascade.sites) && cascade.sites.length !== count) {
    // the run overrode the data centers: use the substations the backend snapped them to
    delete body.lat
    delete body.lon
    delete body.mw
    body.sites = cascade.sites.map((s) => ({ lat: s.sub_lat, lon: s.sub_lon, mw: s.mw }))
  }
  return body
}
