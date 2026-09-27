import { T } from './text'

// DESCRIBE THE MAP (audio-described mode). The presentation is a map with things happening on it; a viewer who is blind
// or has low vision gets the narration but not what the map shows. With "Describe what's on the map" on, every beat gets
// one short plain "On the map: ..." line, written by the server from the beat's own data (backend/describe.py: templates,
// English and Spanish, every figure the deck's own; slide.describe[lang] = {text, key, chars}). This file is the page's
// side of it: the remembered choice, the words around the line, and the deck with each line put first in its beat.
//
// The line reaches the viewer three ways: as text in a polite live region (Describe.jsx, so a screen reader reads it with
// no audio), as the first thing the analyst voice says in the beat when sound is on (the line is a narration segment, so
// the beat waits for it and the progress bar keeps flowing), and in the transcript and downloads.

const KEY = 'overload.describe'

// Off until this viewer turns it on; remembered per browser (storage can come back empty or throw).
export function readDescribe() {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function writeDescribe(on) {
  try {
    localStorage.setItem(KEY, on ? '1' : '0')
  } catch {
    // private window / blocked storage: the choice just isn't remembered
  }
}

export const D = {
  en: {
    toggle: "Describe what's on the map",
    toggleShort: 'Describe the map',
    state: (on) => (on ? 'on' : 'off'),
    prefix: 'On the map:',
    who: 'On the map',
    none: 'Not available for this briefing',
    hint: 'Adds a short spoken and written description of what the map shows in each beat. Turns the presentation slightly longer.',
    group: 'Describe the map',
  },
  es: {
    toggle: 'Describir lo que muestra el mapa',
    toggleShort: 'Describir el mapa',
    state: (on) => (on ? 'sí' : 'no'),
    prefix: 'En el mapa:',
    who: 'En el mapa',
    none: 'No disponible para este informe',
    hint: 'Añade una descripción breve, hablada y escrita, de lo que muestra el mapa en cada momento. Alarga un poco la presentación.',
    group: 'Describir el mapa',
  },
}

const CPS = { en: 14.5, es: 15.5 } // spoken characters per second: the same rate the deck's own estimates use
const GAP_S = 0.35 // the stage's pause between segments

// The description of one beat in one language, or null.
export const descriptionOf = (slide, lang) => slide?.describe?.[lang] || slide?.describe?.en || null

// Does this deck carry descriptions at all? (an older cached deck, or the preview fixture, does not)
export const hasDescriptions = (deck) => !!deck?.slides?.some((s) => s.describe)

// The speaker label of a segment in the transcripts: a description is "On the map", not the analyst.
export const roleLabel = (seg, lang) => (seg?.describe ? D[lang].who : seg?.role === 'analyst' ? T[lang].analyst : T[lang].presenter)

// The deck with every described beat's line as the first segment of its narration, in both languages: the analyst voice
// speaks it (its key is registered with the voice service like the deck's own lines) and the beat waits for it. The beat's
// estimated length grows by the line's own, so the narration's pace is unchanged. A deck without descriptions comes back
// as it is.
export function withDescriptions(deck) {
  if (!hasDescriptions(deck)) return deck
  const slides = deck.slides.map((s) => {
    if (!s.describe) return s
    const narration = { ...(s.narration || {}) }
    const est_s = s.est_s ? { ...s.est_s } : s.est_s
    for (const lang of Object.keys(s.describe)) {
      const d = s.describe[lang]
      if (!d?.text || !d?.key) continue
      narration[lang] = [{ role: 'analyst', text: d.text, chars: d.chars ?? d.text.length, cues: [], key: d.key, describe: true }, ...(narration[lang] || [])]
      if (est_s && est_s[lang] != null) est_s[lang] = Math.round((est_s[lang] + d.text.length / (CPS[lang] || CPS.en) + GAP_S) * 10) / 10
    }
    return { ...s, narration, est_s }
  })
  return { ...deck, slides }
}
