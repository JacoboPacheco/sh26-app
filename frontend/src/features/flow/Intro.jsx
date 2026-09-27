import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import BeginGate from '../../shell/BeginGate'
import { gateEligible, markBegun } from '../../shell/beginRules'
import { useOverload } from '../../store'
import { setIntroPhase } from './introPhase'
import './flow.css'

// FEATURE: the living grid, the opening moment (flow track). Mounted by App.jsx after the map.
//
// The map starts dark with only the question lit in the header; the grid powers on south to north
// behind a pale edge (IntroCurtain, drawn inside the map by FlowCanvas), and a prompt points at the
// campus card: "Drag the AI campus onto Florida". 3.4 s, then it unmounts. It never blocks
// anything: every piece is pointer-events: none, any click, key or wheel skips it, it plays once per
// browser session, and never with reduced motion. Automated browsers (navigator.webdriver) skip it
// so test screenshots show the map; add ?intro to the URL to play it anyway (also how to replay it
// for a demo in the same tab).
//
// On the bare home page it waits for the viewer first (user, Sat 23:38: "a click anywhere to begin that only
// goes away when someone clicks"): phase 'gate' shows the quiet prompt (shell/BeginGate) over the map, dimmed
// but alive, until a click, tap or key; that click begins the opening and does nothing else. See
// shell/beginRules.js for where the gate shows and where it doesn't.

const SEEN_KEY = 'overload:intro-seen'
const OUT_AT_MS = 3050 // the fade-out starts
const FADE_MS = 350 // matches .intro--out's transition
const CUE_ROOM = 260 // px needed right of the campus card for the prompt
const BEGIN_SETTLE_MS = 450 // the click or key that began must not also skip the opening it starts

const hasParam = (name) => {
  try {
    return new URLSearchParams(window.location.search).has(name)
  } catch {
    return false
  }
}

// may the opening play at all (the gate's click starts it whatever the session already saw)
function openingAllowed() {
  if (typeof window === 'undefined') return false
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return false
  return hasParam('intro') || hasParam('begin') || !navigator.webdriver
}

function shouldPlay() {
  if (typeof window === 'undefined') return false
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return false
  if (hasParam('intro')) return true
  if (navigator.webdriver) return false
  try {
    return window.sessionStorage.getItem(SEEN_KEY) !== '1'
  } catch {
    return true // storage blocked: it may play again, which is harmless
  }
}

function markSeen() {
  try {
    window.sessionStorage.setItem(SEEN_KEY, '1')
  } catch {
    // private mode or blocked storage: nothing to remember it in
  }
}

// Where the campus card is, if there's room beside it for the prompt (not on a phone layout).
function findCard() {
  const card = document.querySelector('.dc-chip')
  if (!card) return null
  const r = card.getBoundingClientRect()
  if (!r.width || r.bottom < 0 || r.top > window.innerHeight) return null
  if (window.innerWidth - r.right < CUE_ROOM) return null
  return { left: r.left, top: r.top, width: r.width, height: r.height }
}

export default function Intro() {
  const { grid } = useOverload()
  // gate (waiting for the viewer, bare home page only) -> wait -> play -> out -> off
  const [phase, setPhase] = useState(() => (gateEligible() ? 'gate' : shouldPlay() ? 'wait' : 'off'))
  const [card, setCard] = useState(null)
  const beganAt = useRef(0)

  // the viewer began: start the opening (or, when it can't play, just show the map)
  const begin = useCallback(() => {
    markBegun()
    beganAt.current = performance.now()
    const next = openingAllowed() ? 'wait' : 'off'
    if (next === 'off') markSeen()
    setPhase((p) => (p === 'gate' ? next : p))
  }, [])

  // Share the phase with the curtain inside the map. A layout effect, so the curtain is there
  // before the map is first painted lit; 'off' again if this unmounts.
  useLayoutEffect(() => {
    setIntroPhase(phase)
  }, [phase])
  useLayoutEffect(() => () => setIntroPhase('off'), [])

  // Start the moment the map exists.
  useLayoutEffect(() => {
    if (phase !== 'wait' || !grid) return
    // measuring the DOM before paint is what a layout effect is for; one re-render, before paint
    // oxlint-disable-next-line react/set-state-in-effect
    setCard(findCard())
    setPhase('play')
  }, [phase, grid])

  useEffect(() => {
    if (phase !== 'play') return undefined
    markSeen()
    const t = setTimeout(() => setPhase('out'), OUT_AT_MS)
    return () => clearTimeout(t)
  }, [phase])

  useEffect(() => {
    if (phase !== 'out') return undefined
    const t = setTimeout(() => setPhase('off'), FADE_MS)
    return () => clearTimeout(t)
  }, [phase])

  // Any click, key or wheel skips it. Listeners only watch; the event still does its normal job.
  useEffect(() => {
    if (phase !== 'wait' && phase !== 'play') return undefined
    const skip = () => {
      if (performance.now() - beganAt.current < BEGIN_SETTLE_MS) return
      markSeen()
      setPhase((p) => (p === 'wait' ? 'off' : p === 'play' ? 'out' : p))
    }
    const opts = { capture: true, passive: true }
    window.addEventListener('pointerdown', skip, opts)
    window.addEventListener('keydown', skip, opts)
    window.addEventListener('wheel', skip, opts)
    return () => {
      window.removeEventListener('pointerdown', skip, opts)
      window.removeEventListener('keydown', skip, opts)
      window.removeEventListener('wheel', skip, opts)
    }
  }, [phase])

  if (phase === 'gate') return <BeginGate onBegin={begin} />
  if (!card || (phase !== 'play' && phase !== 'out')) return null
  return (
    <div className={`intro-cue${phase === 'out' ? ' intro--out' : ''}`} aria-hidden="true">
      <div className="intro-ring" style={{ left: card.left - 5, top: card.top - 5, width: card.width + 10, height: card.height + 10 }} />
      <div className="intro-callout" style={{ left: card.left + card.width + 20, top: card.top + card.height / 2 }}>
        <span className="intro-callout__main">Drag the AI campus onto Florida</span>
        <span className="intro-callout__sub">Then see whose lights go out.</span>
      </div>
    </div>
  )
}
