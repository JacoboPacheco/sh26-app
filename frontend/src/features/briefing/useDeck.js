import { useCallback, useEffect, useRef, useState } from 'react'
import { getDeck, getReport, notLive, refetchDeck, refetchReport } from './briefingApi'

const POLL_MS = 5000 // the AI proposer runs in the background (about 15-25 s): ask again this often
const POLL_FOR_MS = 75000 // and for at most this long (the show reaches the solutions after about a minute and a half)

// The report and the slide deck for one case. The template deck (ai=false) opens the stage at once; Gemini's deck
// (ai=true) is asked for at the same moment (PresentDamage and the review card ask for it as soon as a cascade lands,
// so it is usually in before the click) and comes back separately as `aiDeck`: the stage holds its autoplay a moment
// for it and, once playing, swaps in the slides it has not played yet (REVIEW-1 #1: it used to be dropped).
// `aiSettled`: Gemini's deck arrived, or will not (error, not live). `allowFixture` (the preview only): while the
// engine/writer routes aren't live, use the contract-shaped fixture instead, clearly labeled.
//
// `late`: while the AI proposer is still working on the case (deck.agentic.status === 'running') the deck is
// fetched again every few seconds; once it is done, {deck, report} carry its verified plans (Gemini's deck when the
// stage had one, so its written slides stay written). The stage swaps them in if the show has not reached the
// solutions yet.
export default function useDeck(body, { allowFixture = false } = {}) {
  const key = body ? JSON.stringify(body) : null
  const [state, setState] = useState({ key: null, report: null, deck: null, aiDeck: null, aiSettled: false, error: null, fixture: false, late: null })
  const [attempt, setAttempt] = useState(0)
  const bodyRef = useRef(body)
  useEffect(() => {
    bodyRef.current = body
  })

  useEffect(() => {
    if (!key) return undefined
    let live = true
    let timer = 0
    let hasAi = false
    const b = bodyRef.current
    const set = (patch) =>
      live && setState((s) => (s.key === key ? { ...s, ...patch } : { key, report: null, deck: null, aiDeck: null, aiSettled: false, error: null, fixture: false, late: null, ...patch }))
    set({})
    const fallbackToFixture = async (err) => {
      if (!allowFixture || !notLive(err)) throw err
      const fx = (await import('./fixture.json')).default
      const pick = b.preset ? fx.catastrophe : fx.hero
      set({ report: pick.report, deck: pick.deck, fixture: true, error: null, aiSettled: true })
    }
    // the AI proposer's plans: ask again while it runs, stop when it is done (or after a while). A deck whose
    // report has no status yet (agentic: null; two requests raced, or the server rebuilt the report while a
    // run was going) is asked again too: the next ask starts the proposer on the report the server keeps
    const t0 = performance.now()
    const poll = (deck) => {
      const pending = deck && 'agentic' in deck && (!deck.agentic?.status || deck.agentic.status === 'running')
      if (!live || !pending || performance.now() - t0 > POLL_FOR_MS) return
      timer = setTimeout(() => {
        refetchDeck(b, { ai: false })
          .then(async (next) => {
            if (!live) return
            const now = next?.agentic?.status
            if (now === 'running' || !now) return poll(next)
            if (now !== 'done' || !next?.slides?.length) return // off / error: the engine's own fixes stand
            // Gemini's deck once more when the stage has one (its prose is cached server-side; the plans are new)
            const written = hasAi ? await refetchDeck(b, { ai: true }).catch(() => null) : null
            const report = await refetchReport(b).catch(() => null)
            if (live) set({ late: { deck: written?.slides?.length ? written : next, report } })
          })
          .catch(() => {}) // the plans are a bonus: the engine's own fixes stand
      }, POLL_MS)
    }
    getReport(b)
      .then((report) => set({ report }))
      .catch(() => {}) // the deck carries what the stage needs; the report adds the written document
    getDeck(b, { ai: false })
      .then((deck) => {
        set({ deck, error: null })
        poll(deck)
      })
      .catch((err) => fallbackToFixture(err).catch((e) => set({ error: e })))
    // Gemini's deck, at the same time (not after the template: every millisecond counts before autoplay)
    getDeck(b, { ai: true })
      .then((aiDeck) => {
        hasAi = !!aiDeck?.slides?.length
        set(hasAi ? { aiDeck, aiSettled: true } : { aiSettled: true })
      })
      .catch(() => set({ aiSettled: true })) // Gemini's deck is a bonus: the template deck stays
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [key, allowFixture, attempt])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  const mine = state.key === key
  return {
    report: mine ? state.report : null,
    deck: mine ? state.deck : null,
    aiDeck: mine ? state.aiDeck : null,
    aiSettled: mine ? state.aiSettled : false,
    error: mine ? state.error : null,
    fixture: mine && state.fixture,
    late: mine ? state.late : null,
    retry,
  }
}
