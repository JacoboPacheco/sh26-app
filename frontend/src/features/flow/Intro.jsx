import { useEffect, useLayoutEffect, useState } from 'react'
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

const SEEN_KEY = 'overload:intro-seen'
const OUT_AT_MS = 3050 // the fade-out starts
const FADE_MS = 350 // matches .intro--out's transition
const CUE_ROOM = 260 // px needed right of the campus card for the prompt

function shouldPlay() {
  if (typeof window === 'undefined') return false
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return false
  let forced = false
  try {
    forced = new URLSearchParams(window.location.search).has('intro')
  } catch {
    forced = false
  }
  if (forced) return true
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
  const [phase, setPhase] = useState(() => (shouldPlay() ? 'wait' : 'off')) // wait -> play -> out -> off
  const [card, setCard] = useState(null)

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
