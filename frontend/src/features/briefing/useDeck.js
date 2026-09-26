import { useCallback, useEffect, useRef, useState } from 'react'
import { getDeck, getReport, notLive } from './briefingApi'

// The report and the slide deck for one case. The template deck (ai=false) opens the stage at once;
// Gemini's deck (ai=true) replaces it only if it arrives before playback starts (`locked` = started).
// `allowFixture` (the preview only): while the engine/writer routes aren't live, use the contract-shaped
// fixture instead, clearly labeled.
export default function useDeck(body, { allowFixture = false, locked } = {}) {
  const key = body ? JSON.stringify(body) : null
  const [state, setState] = useState({ key: null, report: null, deck: null, error: null, fixture: false })
  const [attempt, setAttempt] = useState(0)
  const bodyRef = useRef(body)
  useEffect(() => {
    bodyRef.current = body
  })

  useEffect(() => {
    if (!key) return undefined
    let live = true
    const b = bodyRef.current
    const set = (patch) => live && setState((s) => (s.key === key ? { ...s, ...patch } : { key, report: null, deck: null, error: null, fixture: false, ...patch }))
    set({})
    const fallbackToFixture = async (err) => {
      if (!allowFixture || !notLive(err)) throw err
      const fx = (await import('./fixture.json')).default
      const pick = b.preset ? fx.catastrophe : fx.hero
      set({ report: pick.report, deck: pick.deck, fixture: true, error: null })
    }
    getReport(b)
      .then((report) => set({ report }))
      .catch(() => {}) // the deck carries what the stage needs; the report adds the written document
    getDeck(b, { ai: false })
      .then((deck) => {
        set({ deck, error: null })
        getDeck(b, { ai: true })
          .then((aiDeck) => {
            if (!live || locked?.current) return
            if (aiDeck?.slides?.length) set({ deck: aiDeck })
          })
          .catch(() => {}) // Gemini's deck is a bonus: the template deck stays
      })
      .catch((err) => fallbackToFixture(err).catch((e) => set({ error: e })))
    return () => {
      live = false
    }
  }, [key, allowFixture, attempt, locked])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  const mine = state.key === key
  return {
    report: mine ? state.report : null,
    deck: mine ? state.deck : null,
    error: mine ? state.error : null,
    fixture: mine && state.fixture,
    retry,
  }
}
